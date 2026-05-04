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
const MAX_ARTICLE_FETCHES = 35;
const MIN_ARTICLE_TEXT_CHARS = 220;
const MIN_SUMMARY_EVIDENCE_CHARS = 160;
const URL_CONTEXT_WINDOW = 260;

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
      const htmlBody = message.getBody() || '';
      const body = message.getPlainBody() || stripHtml_(htmlBody);
      const urlCandidates = extractUrlCandidates_(body, htmlBody)
        .filter(candidate => !isPrivateUtilityUrl_(candidate.url) && (includeAllUrls || isResearchUrl_(candidate.url)));
      const contextByUrl = {};
      const urls = [];
      urlCandidates.forEach(candidate => {
        if (!contextByUrl[candidate.url]) {
          urls.push(candidate.url);
          contextByUrl[candidate.url] = candidate.context;
        } else if (candidate.context && candidate.context.length > contextByUrl[candidate.url].length) {
          contextByUrl[candidate.url] = candidate.context;
        }
      });
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
      rankNewsletterUrls_(urls).slice(0, 5).forEach(url => {
        const shouldFetchDetail = fetchArticleDetails && detailFetchCount < MAX_ARTICLE_FETCHES;
        const articleText = shouldFetchDetail ? fetchArticleText_(url) : '';
        if (shouldFetchDetail) detailFetchCount += 1;
        const sourceContext = contextByUrl[url] || snippet;
        const detailBasis = articleText || sourceContext || snippet;
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
          sourceContext: sourceContext,
          articleText: articleText
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
  Object.keys(grouped).sort((a, b) => detailedItems_(grouped[b]).length - detailedItems_(grouped[a]).length || a.localeCompare(b)).forEach(topic => {
    const detailed = detailedItems_(grouped[topic]);
    if (detailed.length === 0) return;
    lines.push('', '### ' + topic);
    detailed.slice(0, 3).forEach(item => {
      const title = truncate_(plain_(item.title), 90);
      const summary = buildSummaryLines_(item);
      lines.push('- 주요 아티클/논문: ' + title);
      lines.push('  - 핵심: ' + summary[0]);
      lines.push('  - 기술 포인트: ' + summary[1]);
      lines.push('  - 의미/근거: ' + summary[2]);
      if (item.url) {
        lines.push('  - 출처 링크: [' + title.replace(/]/g, '') + '](' + item.url + ')');
      } else {
        lines.push('  - 출처 링크: 메일 본문 내 외부 링크 없음');
      }
    });
    const remaining = detailed.length - 3;
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
    payload: JSON.stringify({ content: content, allowed_mentions: { parse: [] } }),
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




function rankNewsletterUrls_(urls) {
  const unique = [];
  const seen = {};
  urls.forEach(url => {
    const normalized = sanitizeUrl_(url);
    if (!normalized || seen[normalized]) return;
    seen[normalized] = true;
    unique.push(normalized);
  });
  return unique.sort((a, b) => scoreNewsletterUrl_(b) - scoreNewsletterUrl_(a));
}

function scoreNewsletterUrl_(url) {
  const lower = String(url || '').toLowerCase();
  let score = 0;
  const highValueHosts = ['arxiv.org', 'openreview.net', 'aclanthology.org', 'proceedings.mlr.press', 'github.com', 'huggingface.co', 'deepmind.google', 'openai.com', 'anthropic.com'];
  highValueHosts.forEach(h => { if (lower.indexOf(h) !== -1) score += 12; });
  ['/blog/', '/research/', '/paper', '/papers', '/post', '/posts', '/article', '/articles', '/p/'].forEach(p => { if (lower.indexOf(p) !== -1) score += 5; });
  ['linkedin.com/comm/pulse', 'beehiiv.com/p/', 'medium.com/', 'substack.com/p/'].forEach(p => { if (lower.indexOf(p) !== -1) score += 4; });
  ['unsubscribe', 'preferences', 'privacy', 'terms', 'login', 'signin', 'signup', 'account', 'settings', 'share', 'facebook.com', 'twitter.com', 'x.com/', 'instagram.com'].forEach(p => { if (lower.indexOf(p) !== -1) score -= 20; });
  if (/\.(png|jpg|jpeg|gif|webp|svg|css|js)(\?|$)/i.test(lower)) score -= 30;
  return score;
}

function sanitizeUrl_(url) {
  const cleaned = decodeEntities_(String(url || '').replace(/[.,;:!?)]}>'"]+$/g, ''));
  return canonicalPublicUrl_(unwrapTrackedUrl_(cleaned));
}

function canonicalPublicUrl_(url) {
  const text = String(url || '').replace(/[.,;:!?)]}>'"]+$/g, '');
  const parts = text.match(/^(https?:\/\/[^?#]+)(\?[^#]*)?(#.*)?$/i);
  if (!parts) return text;
  const base = parts[1];
  const query = parts[2] ? parts[2].slice(1) : '';
  const hash = parts[3] || '';
  if (!query) return base + hash;
  const keep = [];
  query.split('&').forEach(pair => {
    const key = decodeURIComponentSafe_(pair.split('=')[0] || '').toLowerCase();
    if (!key || isTrackingQueryParam_(key, pair)) return;
    keep.push(pair);
  });
  return base + (keep.length ? '?' + keep.join('&') : '') + hash;
}

function isTrackingQueryParam_(key, pair) {
  if (/^utm_/.test(key)) return true;
  const blocked = [
    'fbclid', 'gclid', 'dclid', 'mc_cid', 'mc_eid', 'igshid', 'vero_id', 'mkt_tok',
    'email', 'email_id', 'subscriber', 'subscriber_id', 'subscription_id', 'user_id', 'uid',
    'token', 'signature', 'sig', 'hash', 'key', 'auth', 'jwt', 'code', 'state', 'session',
    'unsubscribe', 'preferences', 'ref', 'source', 'campaign', 's', 'sk'
  ];
  if (blocked.indexOf(key) !== -1) return true;
  const value = String(pair || '').split('=').slice(1).join('=');
  return value.length > 80 && /[A-Za-z0-9_-]{40,}/.test(value);
}

function unwrapTrackedUrl_(url) {
  const text = String(url || '');
  const direct = decodeURIComponentSafe_(text);
  const params = ['url', 'u', 'q', 'redirect', 'redirect_url', 'target', 'to'];
  for (let i = 0; i < params.length; i++) {
    const re = new RegExp('[?&]' + params[i] + '=([^&#]+)', 'i');
    const match = text.match(re);
    if (match) {
      const decoded = decodeURIComponentSafe_(match[1]);
      if (/^https?:\/\//i.test(decoded) && !isPrivateUtilityUrl_(decoded)) return decoded.replace(/[.,;:!?)]}>'"]+$/g, '');
    }
  }
  const embedded = direct.match(/https?:\/\/[A-Za-z0-9][^\s<>()"'\]]+/g) || [];
  if (embedded.length > 1) {
    const last = embedded[embedded.length - 1].replace(/[.,;:!?)]}>'"]+$/g, '');
    if (!isPrivateUtilityUrl_(last)) return last;
  }
  return text;
}

function decodeURIComponentSafe_(text) {
  try {
    return decodeURIComponent(String(text || '').replace(/&amp;/g, '&'));
  } catch (e) {
    return String(text || '');
  }
}

function articleFetchUrl_(url) {
  const text = String(url || '');
  const arxivPdf = text.match(/^https?:\/\/(?:www\.)?arxiv\.org\/pdf\/([^?#]+)(?:\.pdf)?/i);
  if (arxivPdf) return 'https://arxiv.org/abs/' + arxivPdf[1].replace(/\.pdf$/i, '');
  return text;
}

function fetchArticleText_(url) {
  const fetchUrl = articleFetchUrl_(url);
  if (!isFetchableArticleUrl_(fetchUrl)) return '';
  try {
    const response = UrlFetchApp.fetch(fetchUrl, {
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
    return extractArticleText_(response.getContentText()).slice(0, MAX_ARTICLE_CHARS);
  } catch (e) {
    return '';
  }
}

function isFetchableArticleUrl_(url) {
  const lower = String(url || '').toLowerCase();
  if (!/^https:\/\//.test(lower)) return false;
  const hostMatch = lower.match(/^https:\/\/([^\/:?#]+)/);
  const host = hostMatch ? hostMatch[1] : '';
  if (!host || isPrivateHost_(host)) return false;
  const blocked = [
    'linkedin.com',
    'mail.google.com',
    'accounts.google.com',
    'localhost',
    '127.0.0.1',
    '0.0.0.0',
    'click.',
    'tracking.',
    'track.',
    'mandrillapp.com',
    'list-manage.com'
  ];
  return !blocked.some(token => host.indexOf(token) !== -1 || lower.indexOf(token) !== -1);
}

function isPrivateHost_(host) {
  const lower = String(host || '').toLowerCase();
  if (lower === 'localhost' || lower.endsWith('.local') || lower.endsWith('.internal')) return true;
  const ip = lower.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (!ip) return false;
  const a = Number(ip[1]);
  const b = Number(ip[2]);
  if (a === 10 || a === 127 || a === 0 || a === 169 && b === 254) return true;
  if (a === 172 && b >= 16 && b <= 31) return true;
  if (a === 192 && b === 168) return true;
  return false;
}

function extractArticleText_(html) {
  const raw = String(html || '');
  const candidates = [
    extractJsonLdArticleBody_(raw),
    extractTaggedContent_(raw, 'article'),
    extractTaggedContent_(raw, 'main'),
    extractMetaDescription_(raw),
    extractReadableText_(raw)
  ].map(plain_).filter(Boolean);
  const best = candidates.sort((a, b) => b.length - a.length)[0] || '';
  const meta = extractMetaDescription_(raw);
  const combined = meta && best.indexOf(meta) === -1 ? meta + '. ' + best : best;
  return articleSentences_(combined).join('. ');
}

function extractJsonLdArticleBody_(html) {
  const blocks = String(html || '').match(/<script[^>]+type=["']application\/ld\+json["'][^>]*>[\s\S]*?<\/script>/gi) || [];
  for (let i = 0; i < blocks.length; i++) {
    const json = decodeEntities_(blocks[i].replace(/<script[^>]*>/i, '').replace(/<\/script>/i, '').trim());
    const parsed = parseJsonLd_(json);
    const extracted = findJsonLdText_(parsed);
    if (extracted) return extracted;
  }
  return '';
}

function parseJsonLd_(json) {
  try {
    return JSON.parse(json);
  } catch (e) {
    return null;
  }
}

function findJsonLdText_(node) {
  if (!node) return '';
  if (Array.isArray(node)) {
    for (let i = 0; i < node.length; i++) {
      const found = findJsonLdText_(node[i]);
      if (found) return found;
    }
    return '';
  }
  if (typeof node !== 'object') return '';
  if (typeof node.articleBody === 'string' && node.articleBody.length > 80) return node.articleBody;
  if (typeof node.description === 'string' && node.description.length > 80) return node.description;
  if (node['@graph']) return findJsonLdText_(node['@graph']);
  const keys = Object.keys(node);
  for (let i = 0; i < keys.length; i++) {
    const found = findJsonLdText_(node[keys[i]]);
    if (found) return found;
  }
  return '';
}

function decodeJsonString_(text) {
  try {
    return JSON.parse('"' + String(text || '').replace(/"/g, '\\"') + '"');
  } catch (e) {
    return String(text || '').replace(/\\n/g, ' ').replace(/\\"/g, '"');
  }
}

function extractTaggedContent_(html, tag) {
  const re = new RegExp('<' + tag + '[^>]*>([\\s\\S]*?)<\\/' + tag + '>', 'i');
  const match = String(html || '').match(re);
  return match ? extractReadableText_(match[1]) : '';
}

function extractMetaDescription_(html) {
  const text = String(html || '');
  const patterns = [
    /<meta[^>]+property=["']og:description["'][^>]+content=["']([\s\S]*?)["'][^>]*>/i,
    /<meta[^>]+name=["']description["'][^>]+content=["']([\s\S]*?)["'][^>]*>/i,
    /<meta[^>]+content=["']([\s\S]*?)["'][^>]+property=["']og:description["'][^>]*>/i,
    /<meta[^>]+content=["']([\s\S]*?)["'][^>]+name=["']description["'][^>]*>/i
  ];
  for (let i = 0; i < patterns.length; i++) {
    const match = text.match(patterns[i]);
    if (match) return decodeEntities_(match[1]);
  }
  return '';
}

function extractReadableText_(html) {
  let text = String(html || '');
  text = text.replace(/<script[\s\S]*?<\/script>/gi, ' ');
  text = text.replace(/<style[\s\S]*?<\/style>/gi, ' ');
  text = text.replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ');
  text = text.replace(/<\/(p|div|section|article|main|h1|h2|h3|li)>/gi, '. ');
  text = text.replace(/<br\s*\/?>(?![^<]*>)/gi, '. ');
  text = text.replace(/<(header|nav|footer|aside|form|button|svg|iframe)[\s\S]*?<\/\1>/gi, ' ');
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
    .replace(/&#39;/g, "'")
    .replace(/&rsquo;/g, "'")
    .replace(/&lsquo;/g, "'")
    .replace(/&ldquo;/g, '"')
    .replace(/&rdquo;/g, '"')
    .replace(/&mdash;/g, '—')
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCharCode(parseInt(n, 16)));
}

function detailedItems_(items) {
  return items
    .map(item => {
      item.summaryLines = buildSummaryLines_(item);
      return item;
    })
    .filter(item => item.summaryLines.length === 3);
}

function hasArticleDetail_(item) {
  return buildSummaryLines_(item).length === 3;
}

function buildSummaryLines_(item) {
  if (item.summaryLines && item.summaryLines.length === 3) return item.summaryLines;
  const publicEvidence = item.articleText || '';
  const selected = selectRoleSummaryLines_(publicEvidence, item.topic || '', item.title || '', item.url || '');
  return selected.length === 3 ? selected.map(s => truncate_(cleanSummarySentence_(s), 135)) : [];
}

function selectRoleSummaryLines_(text, topic, title, url) {
  const sentences = articleSentences_(text);
  if (sentences.length < 3) return buildFallbackSummaryLines_(text, topic, title, url, sentences);
  const roles = [
    { name: 'core', terms: ['announce', 'announces', 'launch', 'launches', 'release', 'releases', 'introduce', 'introduces', 'propose', 'proposes', 'present', 'presents', 'new', 'first', '핵심', '공개', '출시', '발표', '제안'] },
    { name: 'technical', terms: ['architecture', 'framework', 'model', 'dataset', 'training', 'inference', 'retrieval', 'rag', 'agent', 'benchmark', 'evaluation', 'github', 'open-source', 'method', '방법', '모델', '데이터셋', '프레임워크', '아키텍처', '평가', '벤치마크', '검색', '추론'] },
    { name: 'impact', terms: ['improve', 'improves', 'outperform', 'outperforms', 'reduce', 'reduces', 'increase', 'increases', 'performance', 'accuracy', 'latency', 'cost', 'result', 'results', 'impact', '성능', '개선', '결과', '정확도', '비용', '지연', '효과', '한계'] }
  ];
  const picked = [];
  roles.forEach(role => {
    const candidate = bestSentenceForRole_(sentences, role, picked, topic, title);
    if (candidate) picked.push(candidate);
  });
  if (picked.length < 3) {
    const fallback = selectSubstantiveSentences_(text, topic, title);
    fallback.forEach(sentence => {
      if (picked.length < 3 && picked.indexOf(sentence) === -1) picked.push(sentence);
    });
  }
  return picked.length === 3 ? picked : [];
}

function buildFallbackSummaryLines_(text, topic, title, url, sentences) {
  const evidence = plain_(text);
  if (evidence.length < MIN_SUMMARY_EVIDENCE_CHARS || sentences.length === 0) return [];
  const first = sentences[0];
  const technical = bestKeywordSentence_(sentences, ['model', 'dataset', 'training', 'inference', 'retrieval', 'rag', 'agent', 'benchmark', 'evaluation', 'architecture', 'framework', 'github', '모델', '데이터셋', '추론', '검색', '평가', '벤치마크']) || sentences[Math.min(1, sentences.length - 1)];
  const impact = bestKeywordSentence_(sentences, ['improve', 'outperform', 'performance', 'accuracy', 'latency', 'cost', 'result', 'results', 'reduce', 'increase', '성능', '개선', '정확도', '비용', '결과', '한계']) || sentences[Math.min(2, sentences.length - 1)];
  const domain = publicHostLabel_(url);
  const titleHint = plain_(title).replace(/^re:\s*/i, '');
  return [
    first,
    technical !== first ? technical : (domain + ' 공개 본문에서 ' + summarizeTechnicalTerms_(evidence, topic) + ' 관련 단서가 확인됩니다'),
    impact !== first && impact !== technical ? impact : (titleHint ? titleHint + '의 공개 원문이 위 기술 신호를 근거로 다룹니다' : domain + ' 공개 원문이 위 기술 신호를 근거로 다룹니다')
  ];
}

function bestKeywordSentence_(sentences, keywords) {
  let best = '';
  let bestScore = 0;
  sentences.forEach(sentence => {
    const lower = sentence.toLowerCase();
    let score = 0;
    keywords.forEach(keyword => { if (lower.indexOf(keyword) !== -1) score += 1; });
    if (/\d+(\.\d+)?\s*(%|x|×|k|m|b|ms|s|tokens?|parameters?|benchmarks?|datasets?)/i.test(sentence)) score += 1;
    if (score > bestScore) {
      best = sentence;
      bestScore = score;
    }
  });
  return best;
}

function summarizeTechnicalTerms_(text, topic) {
  const lower = String(text || '').toLowerCase();
  const terms = ['RAG', 'retrieval', 'agent', 'LLM', 'multimodal', 'benchmark', 'inference', 'dataset', 'model', 'evaluation', 'GitHub'];
  const hits = terms.filter(term => lower.indexOf(term.toLowerCase()) !== -1).slice(0, 3);
  if (hits.length > 0) return hits.join('/');
  return topic || '기술';
}

function publicHostLabel_(url) {
  const match = String(url || '').match(/^https?:\/\/([^\/]+)/i);
  return match ? match[1].replace(/^www\./i, '') : '공개 링크';
}

function bestSentenceForRole_(sentences, role, picked, topic, title) {
  let best = null;
  let bestScore = -999;
  sentences.forEach((sentence, idx) => {
    if (picked.indexOf(sentence) !== -1) return;
    let score = scoreSentence_(sentence, idx, topic, title);
    const lower = sentence.toLowerCase();
    role.terms.forEach(term => { if (lower.indexOf(term) !== -1) score += 6; });
    if (score > bestScore) {
      best = sentence;
      bestScore = score;
    }
  });
  return bestScore > 0 ? best : null;
}

function selectSubstantiveSentences_(text, topic, title) {
  const sentences = articleSentences_(text);
  const seen = {};
  const scored = [];
  sentences.forEach((sentence, idx) => {
    const normalized = normalizeSentenceKey_(sentence);
    if (seen[normalized]) return;
    seen[normalized] = true;
    const score = scoreSentence_(sentence, idx, topic, title);
    if (score > 0) scored.push({ sentence: sentence, idx: idx, score: score });
  });
  return scored
    .sort((a, b) => b.score - a.score || a.idx - b.idx)
    .slice(0, 6)
    .sort((a, b) => a.idx - b.idx)
    .slice(0, 3)
    .map(x => x.sentence);
}

function normalizeSentenceKey_(sentence) {
  return String(sentence || '').toLowerCase().replace(/[^a-z0-9가-힣]+/g, ' ').trim().slice(0, 120);
}

function scoreSentence_(sentence, idx, topic, title) {
  const text = plain_(sentence);
  const lower = text.toLowerCase();
  if (text.length < 45 || text.length > 420) return -10;
  if (isBoilerplateSentence_(text)) return -10;
  let score = 0;
  if (idx < 18) score += Math.max(0, 10 - idx * 0.35);
  const strongTerms = [
    'propose', 'proposes', 'introduce', 'introduces', 'present', 'presents', 'show', 'shows', 'find', 'finds',
    'improve', 'improves', 'outperform', 'outperforms', 'benchmark', 'evaluation', 'architecture', 'framework',
    'agent', 'agents', 'rag', 'retrieval', 'llm', 'multimodal', 'model', 'training', 'inference', 'dataset',
    'open-source', 'open source', 'github', 'safety', 'alignment', 'reasoning', 'ranking', 'search', 'graph',
    '제안', '개선', '성능', '평가', '모델', '데이터셋', '프레임워크', '아키텍처', '검색', '그래프', '추론', '에이전트', '벤치마크'
  ];
  strongTerms.forEach(term => { if (lower.indexOf(term) !== -1) score += 3; });
  const titleTerms = plain_(title).toLowerCase().split(/\s+/).filter(w => w.length >= 5).slice(0, 8);
  titleTerms.forEach(term => { if (lower.indexOf(term) !== -1) score += 1.5; });
  const topicTerms = String(topic || '').toLowerCase().split(/[\/\s·]+/).filter(Boolean);
  topicTerms.forEach(term => { if (lower.indexOf(term) !== -1) score += 1.5; });
  if (/\d+(\.\d+)?\s*(%|x|×|k|m|b|ms|s|tokens?|parameters?|benchmarks?|datasets?)/i.test(text)) score += 3;
  if (/[가-힣]/.test(text) && /(이다|한다|했다|제안|분석|개선|가능|필요|중요)/.test(text)) score += 2;
  if (/\b(i|we|our|you|your)\b/i.test(text)) score -= 1;
  return score;
}

function articleSentences_(text) {
  return String(text || '')
    .replace(/([.!?。])\s*(?=[A-Z가-힣0-9])/g, '$1\n')
    .split(/\n+|[。]\s+/)
    .map(cleanSummarySentence_)
    .filter(s => s.length >= 35 && !isBoilerplateSentence_(s));
}

function cleanSummarySentence_(text) {
  return plain_(text)
    .replace(/\s*([|•·])\s*/g, ' ')
    .replace(/\s*\.\s*\.\s*\.+/g, '.')
    .replace(/\s+([,.;:!?])/g, '$1')
    .replace(/([.!?]){2,}/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

function isBoilerplateSentence_(text) {
  const lower = String(text || '').toLowerCase();
  const bad = [
    'cookie', 'privacy policy', 'terms of use', 'subscribe', 'login', 'sign in', 'sign up', 'accept', 'decline',
    'advertise', 'advertisement', 'home posts', 'read our', 'read more', 'all rights reserved', 'view in browser', 'unsubscribe', 'manage preferences',
    'share this post', 'share this', 'follow us', 'related posts', 'comments', 'copyright', 'sponsored by', 'thanks for reading', 'open in app', 'not your computer', 'forgot email',
    'google account', 'linkedin에서', '구독', '로그인', '개인정보', '이용약관', '광고', '쿠키'
  ];
  if (bad.some(token => lower.indexOf(token) !== -1)) return true;
  if (/^(by|글쓴이|작성자)\s+/i.test(lower)) return true;
  if (/^.{0,50}(newsletter|home|posts|authors|upgrade)(\s+|$)/i.test(lower)) return true;
  return false;
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
  return extractUrlCandidates_(text, '').map(candidate => candidate.url);
}

function extractUrlCandidates_(plainText, html) {
  const candidates = [];
  const text = String(plainText || '');
  const urlRe = /https?:\/\/[^\s<>()"'\]]+/gi;
  let match;
  while ((match = urlRe.exec(text)) !== null) {
    const url = sanitizeUrl_(match[0]);
    candidates.push({ url: url, context: contextAround_(text, match.index, match[0].length) });
  }

  const htmlText = String(html || '');
  const hrefRe = /<a\b[^>]*href=["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;
  while ((match = hrefRe.exec(htmlText)) !== null) {
    const url = sanitizeUrl_(match[1]);
    const label = plain_(extractReadableText_(match[2] || ''));
    const context = label || contextAround_(stripHtml_(htmlText), match.index, match[0].length);
    candidates.push({ url: url, context: truncate_(context, URL_CONTEXT_WINDOW) });
  }

  const seen = {};
  return candidates.filter(candidate => {
    if (!candidate.url || !/^https?:\/\//i.test(candidate.url)) return false;
    if (seen[candidate.url]) return false;
    seen[candidate.url] = true;
    candidate.context = truncate_(plain_(candidate.context), URL_CONTEXT_WINDOW);
    return true;
  });
}

function contextAround_(text, index, length) {
  const source = plain_(text);
  const start = Math.max(0, index - Math.floor(URL_CONTEXT_WINDOW / 2));
  const end = Math.min(source.length, index + length + Math.floor(URL_CONTEXT_WINDOW / 2));
  return source.slice(start, end);
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
