/**
 * Gmail newsletter archive briefing to Discord.
 *
 * Privacy boundary:
 * - Reads Gmail only inside the user's own Google Apps Script runtime.
 * - Posts only subject/from/date/extracted source URLs, not full email bodies.
 * - Requires SENDER_ALLOWLIST before processing mail.
 *
 * Required Script Properties:
 * - DISCORD_WEBHOOK_URL: Discord channel webhook URL for 아카이브룸/뉴스레타-수집 (recommended)
 * - SENDER_ALLOWLIST: comma-separated sender/domain substrings
 *
 * Optional Script Properties:
 * - DISCORD_CHANNEL_ID: Discord channel snowflake fallback
 * - DISCORD_BOT_TOKEN: Discord bot token fallback; Apps Script may hit Discord/Cloudflare 40333
 * - GMAIL_QUERY: Gmail search query, default newer_than:7d
 * - MAX_THREADS: default 50
 */

const DEFAULT_DISCORD_CHANNEL_ID = '1500839270921801879';
const DEFAULT_GMAIL_QUERY = 'newer_than:7d';
const DEFAULT_MAX_THREADS = 50;

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
  const webhookUrl = props.getProperty('DISCORD_WEBHOOK_URL') || '';
  const channelId = props.getProperty('DISCORD_CHANNEL_ID') || DEFAULT_DISCORD_CHANNEL_ID;
  const token = props.getProperty('DISCORD_BOT_TOKEN') || '';
  const senderAllowlist = csv_(props.getProperty('SENDER_ALLOWLIST') || '');
  const query = props.getProperty('GMAIL_QUERY') || DEFAULT_GMAIL_QUERY;
  const maxThreads = Number(props.getProperty('MAX_THREADS') || DEFAULT_MAX_THREADS);

  if (!webhookUrl && !token) {
    throw new Error('DISCORD_WEBHOOK_URL is recommended; otherwise DISCORD_BOT_TOKEN is required as fallback.');
  }
  if (senderAllowlist.length === 0) {
    throw new Error('SENDER_ALLOWLIST is required; refusing to sweep private mail.');
  }

  const items = collectNewsletterItems_(query, maxThreads, senderAllowlist);
  const briefing = renderBriefing_(items, query);
  postDiscord_(channelId, token, briefing, webhookUrl);
}

function collectNewsletterItems_(query, maxThreads, senderAllowlist) {
  const threads = GmailApp.search(query, 0, maxThreads);
  const seen = {};
  const items = [];

  threads.forEach(thread => {
    thread.getMessages().forEach(message => {
      const sender = message.getFrom() || '';
      if (!matchesAllowlist_(sender, senderAllowlist)) {
        return;
      }
      const subject = message.getSubject() || '(untitled newsletter item)';
      const receivedAt = Utilities.formatDate(message.getDate(), Session.getScriptTimeZone(), "yyyy-MM-dd HH:mm");
      const body = message.getPlainBody() || stripHtml_(message.getBody() || '');
      extractUrls_(body).forEach(url => {
        if (!isResearchUrl_(url)) {
          return;
        }
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
          topic: classifyTopic_(subject + ' ' + url)
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
    '- 기준: allowlist로 허용한 발신자/도메인의 Gmail 뉴스레터',
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
      lines.push('- 핵심 요약: ' + title);
      lines.push('- 기술 포인트: `' + item.kind + '` 유형. 발신자 `' + truncate_(plain_(item.sender), 70) + '`, 수신일 `' + item.receivedAt + '` 기준으로 추적');
      lines.push('- 출처 링크: [' + title.replace(/]/g, '') + '](' + item.url + ')');
    });
    const remaining = grouped[topic].length - 3;
    if (remaining > 0) {
      lines.push('- 추가 항목: ' + remaining + '개는 다음 실행에서 재검토');
    }
  });

  lines.push('', '━━━━━━━━━━━━━━━━━━━━', '원문 메일 본문은 Discord에 게시하지 않습니다.');
  return truncate_(lines.join('\n'), 1900);
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
