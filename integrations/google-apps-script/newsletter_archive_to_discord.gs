/**
 * Gmail newsletter archive briefing to Discord.
 *
 * Privacy boundary:
 * - Reads Gmail only inside the user's own Google Apps Script runtime.
 * - Posts only subject/from/date/extracted source URLs, not full email bodies.
 * - Supports explicit all-mail collection for recent mail windows. Full email bodies are used only in memory for topic extraction/snippets and are not posted verbatim.
 *
 * Required Script Properties:
 * - SENDER_ALLOWLIST: comma-separated sender/domain substrings, unless COLLECT_ALL_MAIL=true
 *
 * Recommended Script Properties for 40333-safe operation:
 * - DELIVERY_MODE: relay_pull
 * - RELAY_READ_TOKEN: shared token used by the EC2 puller
 *
 * Optional Script Properties:
 * - DISCORD_WEBHOOK_URL: direct Discord webhook fallback; may hit Discord/Cloudflare 40333
 * - DISCORD_CHANNEL_ID: Discord channel snowflake bot-token fallback
 * - DISCORD_BOT_TOKEN: bot-token fallback; may hit Discord/Cloudflare 40333
 * - GMAIL_QUERY: Gmail search query, default newer_than:7d
 * - MAX_THREADS: default 50
 * - COLLECT_ALL_MAIL: true to process all messages matching GMAIL_QUERY
 * - INCLUDE_ALL_URLS: true to include non-research URLs
 * - FETCH_ARTICLE_DETAILS: true to fetch public article pages for richer summaries
 */

const DEFAULT_DISCORD_CHANNEL_ID = '1500839270921801879';
const DEFAULT_GMAIL_QUERY = 'newer_than:7d';
const DEFAULT_MAX_THREADS = 50;
const DEFAULT_COLLECT_ALL_MAIL = false;
const DEFAULT_INCLUDE_ALL_URLS = true;
const DEFAULT_FETCH_ARTICLE_DETAILS = true;
const MAX_ARTICLE_CHARS = 5000;
const MAX_ARTICLE_FETCHES = 20;

const RESEARCH_HOST_HINTS = [
  'arxiv.org',
  'doi.org',
  'openreview.net',
  'semanticscholar.org',
  'paperswithcode.com',
  'aclanthology.org',
  'proceedings.mlr.press',
  'neurips.cc',
  'icml.cc',
  'openai.com',
  'anthropic.com',
  'deepmind.google',
  'ai.googleblog.com',
  'github.com',
  'huggingface.co'
];

const TOPIC_RULES = [
  ['검색/RAG/지식그래프', ['retrieval', 'rag', 'search', 'graph', 'knowledge']],
  ['LLM/에이전트', ['llm', 'language model', 'agent', 'tool use', 'reasoning']],
  ['멀티모달/비전', ['multimodal', 'vision', 'image', 'video', 'vlm']],
  ['인프라/배포', ['inference', 'serving', 'gpu', 'cuda', 'deploy', 'latency', 'benchmark']],
  ['오픈소스/코드', ['github.com', 'open source', 'repo', 'library', 'framework']],
  ['AI 안전/평가', ['safety', 'eval', 'alignment', 'red team', 'benchmark']],
  ['산업/제품 동향', ['product', 'launch', 'release', 'pricing', 'market', 'enterprise']]
];

function runNewsletterArchive() {
  const props = PropertiesService.getScriptProperties();
  const deliveryMode = props.getProperty('DELIVERY_MODE') || 'relay_pull';
  const webhookUrl = props.getProperty('DISCORD_WEBHOOK_URL') || '';
  const channelId = props.getProperty('DISCORD_CHANNEL_ID') || DEFAULT_DISCORD_CHANNEL_ID;
  const token = props.getProperty('DISCORD_BOT_TOKEN') || '';
  const senderAllowlist = csv_(props.getProperty('SENDER_ALLOWLIST') || '');
  const collectAllMail = bool_(props.getProperty('COLLECT_ALL_MAIL'), DEFAULT_COLLECT_ALL_MAIL);
  const includeAllUrls = bool_(props.getProperty('INCLUDE_ALL_URLS'), DEFAULT_INCLUDE_ALL_URLS);
  const fetchArticleDetails = bool_(props.getProperty('FETCH_ARTICLE_DETAILS'), DEFAULT_FETCH_ARTICLE_DETAILS);
  const query = props.getProperty('GMAIL_QUERY') || DEFAULT_GMAIL_QUERY;
  const maxThreads = Number(props.getProperty('MAX_THREADS') || DEFAULT_MAX_THREADS);

  if (deliveryMode !== 'relay_pull' && !webhookUrl && !token) {
    throw new Error('Use DELIVERY_MODE=relay_pull, DISCORD_WEBHOOK_URL, or DISCORD_BOT_TOKEN.');
  }
  if (!collectAllMail && senderAllowlist.length === 0) {
    throw new Error('SENDER_ALLOWLIST is required unless COLLECT_ALL_MAIL=true.');
  }

  const items = collectNewsletterItems_(query, maxThreads, senderAllowlist, collectAllMail, includeAllUrls, fetchArticleDetails);
  const briefing = renderBriefing_(items, query);
  saveLatestBriefing_(briefing, items.length, query);
  if (deliveryMode !== 'relay_pull') {
    postDiscord_(channelId, token, briefing, webhookUrl);
  }
  return briefing;
}

function collectNewsletterItems_(query, maxThreads, senderAllowlist, collectAllMail, includeAllUrls, fetchArticleDetails) {
  const threads = GmailApp.search(query, 0, maxThreads);
  const seen = {};
  const items = [];
  let detailFetchCount = 0;

  threads.forEach(thread => {
    thread.getMessages().forEach(message => {
      const sender = message.getFrom() || '';
      if (!collectAllMail && !matchesAllowlist_(sender, senderAllowlist)) {
        return;
      }
      const subject = message.getSubject() || '(untitled newsletter item)';
      const receivedAt = Utilities.formatDate(message.getDate(), Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm");
      const body = message.getPlainBody() || stripHtml_(message.getBody() || '');
      const urls = extractUrls_(body).filter(url => !isPrivateUtilityUrl_(url) && (includeAllUrls || isResearchUrl_(url)));
      const topic = classifyTopic_(subject + ' ' + body);
      const snippet = truncate_(plain_(body), 180);
      if (urls.length === 0) {
        const key = subject + '|message|' + receivedAt;
        if (!seen[key]) {
          seen[key] = true;
          items.push({
            title: subject,
            url: '',
            kind: 'mail-summary',
            sender: sender,
            receivedAt: receivedAt,
            topic: topic,
            snippet: snippet
          });
        }
        return;
      }
      urls.slice(0, 3).forEach(url => {
        const shouldFetchDetail = fetchArticleDetails && detailFetchCount < MAX_ARTICLE_FETCHES;
        const articleText = shouldFetchDetail ? fetchArticleText_(url) : '';
        if (shouldFetchDetail) detailFetchCount += 1;
        const detailBasis = articleText || snippet;
        const key = subject + '|' + url;
        if (seen[key]) {
          return;
        }
        seen[key] = true;
        items.push({
          title: subject,
          url: url,
          kind: classifyUrl_(url),
          sender: sender,
          receivedAt: receivedAt,
          topic: classifyTopic_(subject + ' ' + detailBasis + ' ' + url),
          snippet: snippet,
          articleText: detailBasis
        });
      });
    });
  });

  return items;
}

function renderBriefing_(items, query) {
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  const lines = [
    '**집현전-Claw 뉴스레터 수집 브리핑**',
    '_date: ' + today + '_',
    '_source: GmailApp search `' + sanitizeInline_(query) + '`_',
    '_privacy: 메일 본문/개인정보는 게시하지 않고 메타데이터와 추출 URL만 사용_',
    '',
    '━━━━━━━━━━━━━━━━━━━━',
    '## 토픽별 기술 리포트/뉴스레터 요약',
    '',
    '- 수집 항목: ' + items.length + '개',
    '- 기준: Gmail 검색 조건에 맞는 최근 메일 전체를 수집 후 토픽별 정리',
    '- 운영 메모: Google Apps Script 내부에서 실행되며 Discord에는 요약과 출처 링크만 게시'
  ];

  if (items.length === 0) {
    lines.push('', '### 수집 결과 없음');
    lines.push('- 핵심 요약: 설정된 allowlist와 연구/테크 URL 조건에 맞는 항목이 없습니다.');
    lines.push('- 기술 포인트: SENDER_ALLOWLIST, GMAIL_QUERY, 메일 수신 상태를 점검해야 합니다.');
    lines.push('- 출처 링크: 없음');
    return lines.join('\n');
  }

  const grouped = groupByTopic_(items);
  Object.keys(grouped).sort((a, b) => grouped[b].length - grouped[a].length || a.localeCompare(b)).forEach(topic => {
    lines.push('', '### ' + topic);
    grouped[topic].slice(0, 3).forEach(item => {
      const title = truncate_(plain_(item.title), 90);
      const snippet = plain_(item.articleText || item.snippet || '');
      lines.push('- 주요 아티클/논문: ' + title);
      lines.push('  - 3줄 요약: ' + summarizeLine1_(title, snippet));
      lines.push('  - 3줄 요약: ' + summarizeLine2_(item, snippet));
      lines.push('  - 3줄 요약: ' + summarizeLine3_(item));
      if (item.url) {
        lines.push('  - 출처 링크: [' + title.replace(/]/g, '') + '](' + item.url + ')');
      } else {
        lines.push('  - 출처 링크: 메일 본문 내 외부 링크 없음');
      }
    });
    const remaining = grouped[topic].length - 3;
    if (remaining > 0) {
      lines.push('- 추가 항목: ' + remaining + '개는 다음 실행에서 재검토');
    }
  });

  lines.push('', '━━━━━━━━━━━━━━━━━━━━', '원문 메일 본문은 Discord에 게시하지 않습니다.');
  return truncate_(lines.join('\n'), 1900);
}


function saveLatestBriefing_(briefing, itemCount, query) {
  PropertiesService.getScriptProperties().setProperties({
    LATEST_BRIEFING: briefing,
    LATEST_BRIEFING_AT: new Date().toISOString(),
    LATEST_BRIEFING_ITEM_COUNT: String(itemCount),
    LATEST_BRIEFING_QUERY: query
  });
}

function doGet(e) {
  const props = PropertiesService.getScriptProperties();
  const expected = props.getProperty('RELAY_READ_TOKEN') || '';
  const actual = e && e.parameter ? String(e.parameter.token || '') : '';
  if (!expected || actual !== expected) {
    return ContentService
      .createTextOutput(JSON.stringify({ error: 'unauthorized' }))
      .setMimeType(ContentService.MimeType.JSON);
  }
  return ContentService
    .createTextOutput(JSON.stringify({
      briefing: props.getProperty('LATEST_BRIEFING') || '',
      generated_at: props.getProperty('LATEST_BRIEFING_AT') || '',
      item_count: Number(props.getProperty('LATEST_BRIEFING_ITEM_COUNT') || '0'),
      query: props.getProperty('LATEST_BRIEFING_QUERY') || ''
    }))
    .setMimeType(ContentService.MimeType.JSON);
}

function postDiscord_(channelId, token, content, webhookUrl) {
  const url = webhookUrl || ('https://discord.com/api/v10/channels/' + channelId + '/messages');
  const options = {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ content: content }),
    muteHttpExceptions: true
  };
  if (!webhookUrl) {
    options.headers = { Authorization: 'Bot ' + token };
  }
  const response = UrlFetchApp.fetch(url, options);
  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error('Discord post failed: HTTP ' + status + ' ' + response.getContentText());
  }
}

function installDailyNewsletterTrigger() {
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === 'runNewsletterArchive') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
  ScriptApp.newTrigger('runNewsletterArchive')
    .timeBased()
    .everyDays(1)
    .atHour(8)
    .nearMinute(15)
    .create();
}



function fetchArticleText_(url) {
  if (!isFetchableArticleUrl_(url)) return '';
  try {
    const response = UrlFetchApp.fetch(url, {
      method: 'get',
      followRedirects: true,
      muteHttpExceptions: true,
      headers: {
        'User-Agent': 'Jiphyeonjeon-Claw-NewsletterArchive/1.0 (+research briefing)'
      }
    });
    const status = response.getResponseCode();
    if (status < 200 || status >= 300) return '';
    const headers = response.getAllHeaders ? response.getAllHeaders() : {};
    const contentType = String(headers['Content-Type'] || headers['content-type'] || '').toLowerCase();
    if (contentType && contentType.indexOf('text/html') === -1 && contentType.indexOf('text/plain') === -1) return '';
    return extractReadableText_(response.getContentText()).slice(0, MAX_ARTICLE_CHARS);
  } catch (e) {
    return '';
  }
}

function isFetchableArticleUrl_(url) {
  const lower = String(url || '').toLowerCase();
  if (!/^https?:\/\//.test(lower)) return false;
  const blocked = [
    'linkedin.com',
    'mail.google.com',
    'accounts.google.com',
    'localhost',
    '127.0.0.1'
  ];
  return !blocked.some(host => lower.indexOf(host) !== -1);
}

function extractReadableText_(html) {
  let text = String(html || '');
  text = text.replace(/<script[\s\S]*?<\/script>/gi, ' ');
  text = text.replace(/<style[\s\S]*?<\/style>/gi, ' ');
  text = text.replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ');
  text = text.replace(/<[^>]+>/g, ' ');
  text = decodeEntities_(text);
  return plain_(text);
}

function decodeEntities_(text) {
  return String(text || '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'");
}

function summarizeLine1_(title, snippet) {
  const basis = firstSentence_(snippet) || title;
  return truncate_(basis, 120);
}

function summarizeLine2_(item, snippet) {
  const kind = item.kind || 'post';
  const sender = truncate_(plain_(item.sender), 55);
  const hint = snippet ? extractTechnicalHint_(snippet) : '';
  const second = secondSentence_(snippet);
  return truncate_(second || ('분류 `' + kind + '`; ' + (hint || '발신자 `' + sender + '`의 최신 기술/리서치 항목으로 분류')), 130);
}

function summarizeLine3_(item) {
  return truncate_('토픽 `' + (item.topic || '기타 테크 리포트') + '` 관점에서 후속 읽기/아카이브 대상으로 추적', 120);
}

function firstSentence_(text) {
  const parts = String(text || '').split(/[.!?。]\s+/).map(plain_).filter(Boolean);
  return parts[0] || '';
}

function secondSentence_(text) {
  const parts = String(text || '').split(/[.!?。]\s+/).map(plain_).filter(Boolean);
  return parts[1] || '';
}

function extractTechnicalHint_(text) {
  const lower = String(text || '').toLowerCase();
  const hints = ['agent', 'rag', 'retrieval', 'llm', 'multimodal', 'benchmark', 'inference', 'open source', 'github', 'evaluation', 'safety', 'product'];
  const hit = hints.find(h => lower.indexOf(h) !== -1);
  return hit ? '본문에서 `' + hit + '` 관련 신호 확인' : '';
}

function bool_(value, fallback) {
  if (value === null || value === undefined || String(value).trim() === '') return fallback;
  return ['1', 'true', 'yes', 'on'].indexOf(String(value).trim().toLowerCase()) !== -1;
}

function csv_(value) {
  return String(value || '').split(',').map(v => v.trim().toLowerCase()).filter(Boolean);
}

function matchesAllowlist_(sender, allowlist) {
  const lower = String(sender || '').toLowerCase();
  return allowlist.some(token => lower.indexOf(token) !== -1);
}

function extractUrls_(text) {
  const matches = String(text || '').match(/https?:\/\/[^\s<>()"'\]]+/gi) || [];
  const seen = {};
  return matches.map(url => url.replace(/[.,;:!?)]}>'"]+$/g, '')).filter(url => {
    if (seen[url]) return false;
    seen[url] = true;
    return true;
  });
}

function isPrivateUtilityUrl_(url) {
  const lower = String(url || '').toLowerCase();
  const blocked = [
    'myaccount.google.com',
    'accounts.google.com',
    'mail.google.com',
    'support.google.com/accounts',
    'unsubscribe',
    'preferences',
    'privacy',
    'terms'
  ];
  return blocked.some(token => lower.indexOf(token) !== -1);
}

function isResearchUrl_(url) {
  const lower = String(url || '').toLowerCase();
  return RESEARCH_HOST_HINTS.some(host => lower.indexOf(host) !== -1);
}

function classifyUrl_(url) {
  const lower = String(url || '').toLowerCase();
  if (lower.indexOf('arxiv.org/abs/') !== -1 || lower.indexOf('arxiv.org/pdf/') !== -1) return 'paper:arxiv';
  if (lower.indexOf('doi.org/') !== -1) return 'paper:doi';
  if (['openreview.net', 'semanticscholar.org', 'aclanthology.org', 'proceedings.mlr.press'].some(h => lower.indexOf(h) !== -1)) return 'paper';
  if (lower.indexOf('github.com/') !== -1) return 'code';
  if (['openai.com', 'anthropic.com', 'deepmind.google', 'ai.googleblog.com'].some(h => lower.indexOf(h) !== -1)) return 'research-post';
  return 'post';
}

function classifyTopic_(text) {
  const lower = String(text || '').toLowerCase();
  for (let i = 0; i < TOPIC_RULES.length; i++) {
    const topic = TOPIC_RULES[i][0];
    const needles = TOPIC_RULES[i][1];
    if (needles.some(needle => lower.indexOf(needle) !== -1)) return topic;
  }
  if (lower.indexOf('arxiv.org') !== -1 || lower.indexOf('doi.org') !== -1 || lower.indexOf('openreview.net') !== -1) return '논문/리서치';
  return '기타 테크 리포트';
}

function groupByTopic_(items) {
  return items.reduce((acc, item) => {
    const topic = item.topic || '기타 테크 리포트';
    if (!acc[topic]) acc[topic] = [];
    acc[topic].push(item);
    return acc;
  }, {});
}

function stripHtml_(html) {
  return String(html || '').replace(/<[^>]+>/g, ' ');
}

function plain_(text) {
  return String(text || '').replace(/[\r\n`*_]/g, ' ').replace(/\s+/g, ' ').trim();
}

function sanitizeInline_(text) {
  return plain_(text).replace(/`/g, '');
}

function truncate_(text, max) {
  text = String(text || '');
  return text.length <= max ? text : text.slice(0, Math.max(0, max - 1)).trim() + '…';
}
