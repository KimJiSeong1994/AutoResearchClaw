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
const DEFAULT_COLLECT_ALL_MAIL = true;
const DEFAULT_INCLUDE_ALL_URLS = true;
const DEFAULT_FETCH_ARTICLE_DETAILS = true;
const MAX_ARTICLE_CHARS = 5000;
const MAX_ARTICLE_FETCHES = 35;
const MIN_ARTICLE_TEXT_CHARS = 220;
const MIN_CONTEXT_SENTENCE_CHARS = 45;
const URL_CONTEXT_WINDOW = 260;
const DISCORD_SAFE_CHAR_LIMIT = 1850;
const BRIEFING_RENDER_CHAR_LIMIT = 7600;
const BRIEFING_MAX_TOPICS = 8;
const BRIEFING_MAX_ITEMS_PER_TOPIC = 2;
const BRIEFING_MAX_TOPIC_SHARE = 0.4;

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

const TOPIC_SCORE_THRESHOLD = 2;
const TOPIC_RULES = [
  {
    label: '검색/RAG/지식그래프',
    priority: 10,
    phrases: ['knowledge graph', 'semantic search', 'vector database'],
    terms: ['retrieval', 'rag', 'search', 'graph', 'knowledge']
  },
  {
    label: 'LLM/에이전트',
    priority: 20,
    phrases: ['language model', 'tool use', 'coding agent', 'llm agent'],
    terms: ['llm', 'agent', 'reasoning', 'workflow', 'autonomous']
  },
  {
    label: '멀티모달/비전',
    priority: 30,
    phrases: ['multimodal model'],
    terms: ['multimodal', 'vision', 'image', 'video', 'vlm']
  },
  {
    label: '인프라/배포',
    priority: 40,
    phrases: ['inference serving', 'eval pipeline'],
    terms: ['inference', 'serving', 'gpu', 'cuda', 'deploy', 'latency', 'benchmark']
  },
  {
    label: '오픈소스/코드',
    priority: 50,
    phrases: ['open source', 'developer tool'],
    terms: ['repo', 'library', 'framework'],
    substrings: ['github.com']
  },
  {
    label: 'AI 안전/평가',
    priority: 60,
    phrases: ['red team'],
    terms: ['safety', 'eval', 'alignment', 'privacy', 'security', 'regulation', 'copyright']
  },
  {
    label: '산업/제품 동향',
    priority: 70,
    phrases: ['product launch'],
    terms: ['product', 'launch', 'release', 'pricing', 'market', 'enterprise', 'partnership', 'funding']
  }
];

function runNewsletterArchive() {
  const payload = buildNewsletterArchivePayload_();
  if (payload.delivery_mode !== 'relay_pull') {
    const props = PropertiesService.getScriptProperties();
    postDiscord_(
      props.getProperty('DISCORD_CHANNEL_ID') || DEFAULT_DISCORD_CHANNEL_ID,
      props.getProperty('DISCORD_BOT_TOKEN') || '',
      payload.briefing,
      props.getProperty('DISCORD_WEBHOOK_URL') || ''
    );
  }
  return payload.briefing;
}

function buildNewsletterArchivePayload_() {
  const props = PropertiesService.getScriptProperties();
  const deliveryMode = props.getProperty('DELIVERY_MODE') || 'relay_pull';
  const webhookUrl = props.getProperty('DISCORD_WEBHOOK_URL') || '';
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
  const rendered = renderBriefingWithTelemetry_(items, query);
  const briefing = rendered.briefing;
  saveLatestBriefing_(briefing, rendered.telemetry);
  return {
    briefing: briefing,
    generated_at: rendered.telemetry.generated_at,
    item_count: items.length,
    query: query,
    telemetry: rendered.telemetry,
    delivery_mode: deliveryMode,
    items: sanitizeRelayItems_(items)
  };
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
        if (isBlockedNewsletterItem_(subject, '', emptyArticleDetail_(), snippet)) return;
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
        const articleDetail = shouldFetchDetail ? fetchArticleDetail_(url) : emptyArticleDetail_();
        const articleText = articleDetail.text || '';
        if (shouldFetchDetail) detailFetchCount += 1;
        const sourceContext = contextByUrl[url] || extractUrlContext_(body, url);
        const detailBasis = articleText || articleDetail.title || articleDetail.description || sourceContext || snippet;
        if (isBlockedNewsletterItem_(subject, url, articleDetail, sourceContext)) return;
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
          articleTitle: articleDetail.title,
          articleDescription: articleDetail.description,
          sourceContext: sourceContext,
          articleText: articleText
        });
      });
    });
  });

  return items;
}

function renderBriefing_(items, query) {
  return renderBriefingWithTelemetry_(items, query).briefing;
}

function renderBriefingWithTelemetry_(items, query) {
  const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
  const lines = [
    '**집현전-Claw 기술 브리핑 카드뉴스**',
    '_date: ' + today + '_',
    '_source: GmailApp search `' + sanitizeInline_(query) + '`_',
    '_privacy: 메일 본문/비밀값은 게시하지 않고 공개 아티클 근거와 출처 링크만 사용_',
    '',
    '━━━━━━━━━━━━━━━━━━━━',
    '## 오늘의 카드뉴스 흐름',
    '',
    '- 훅: ' + items.length + '개 공개 링크에서 연구자에게 바로 읽을 변화 신호를 선별',
    '- 구조: 훅 → 맥락 → 핵심 변화 → 왜 중요한가 → 근거 → 시사점 → CTA/저장 포인트',
    '- 운영: Google Apps Script 내부에서 실행되며 Discord에는 compact Markdown 카드만 게시'
  ];

  if (items.length === 0) {
    lines.push('', '### 카드 0 · 수집 결과 없음');
    lines.push('- 훅: 오늘 카드로 만들 공개 연구/기술 링크가 없습니다.');
    lines.push('- 맥락: 설정된 allowlist와 연구/테크 URL 조건에 맞는 항목이 없습니다.');
    lines.push('- 핵심 변화: 새로운 후보가 없어 토픽 다양성 및 요약 품질을 산출하지 않았습니다.');
    lines.push('- 왜 중요한가: 수집 경계가 너무 좁거나 최근 메일 수신이 없을 수 있습니다.');
    lines.push('- 근거: 공개 출처 링크 없음');
    lines.push('- 시사점: SENDER_ALLOWLIST, GMAIL_QUERY, 메일 수신 상태를 점검해야 합니다.');
    lines.push('- CTA/저장 포인트: 다음 실행 전 수집 조건을 재검토');
    return { briefing: lines.join('\n'), telemetry: buildBriefingTelemetry_(items, query, [], 0, false) };
  }

  const grouped = groupByTopic_(items);
  const topics = Object.keys(grouped)
    .map(topic => ({ topic: topic, detailed: detailedItems_(grouped[topic]), total: grouped[topic].length }))
    .filter(entry => entry.detailed.length > 0)
    .sort((a, b) => b.detailed.length - a.detailed.length || b.total - a.total || a.topic.localeCompare(b.topic));

  const selectedTopics = topics.slice(0, BRIEFING_MAX_TOPICS);
  const topicOverview = selectedTopics.map(entry => entry.topic + ' ' + entry.detailed.length + '/' + entry.total).join(' · ');
  if (topicOverview) {
    appendWithinLimit_(lines, ['', '토픽 인덱스: ' + topicOverview], BRIEFING_RENDER_CHAR_LIMIT);
  }

  let renderedItems = 0;
  let renderTruncated = false;
  let cardNo = 0;
  const renderedTopicCounts = {};
  selectedTopics.forEach((entry, topicIdx) => {
    const topicHeader = ['', '━━━━━━━━━━━━━━━━━━━━', '### 섹션 ' + (topicIdx + 1) + '. ' + entry.topic, '- 토픽 내 후보: ' + entry.total + '개'];
    if (!appendWithinLimit_(lines, topicHeader, BRIEFING_RENDER_CHAR_LIMIT)) {
      renderTruncated = true;
      return;
    }
    const itemsToRender = entry.detailed.slice(0, BRIEFING_MAX_ITEMS_PER_TOPIC);
    itemsToRender.forEach(item => {
      cardNo += 1;
      const title = truncate_(plain_(item.articleTitle || item.title), 90);
      const summary = buildSummaryLines_(item);
      const evidence = summarizeTechnicalTerms_((item.articleText || item.articleDescription || item.snippet || item.title || '') + ' ' + item.url, entry.topic);
      const sourceMeta = sanitizeInline_(item.sender || 'unknown') + ' · ' + sanitizeInline_(item.receivedAt || 'unknown') + ' · `' + sanitizeInline_(item.kind || 'post') + '`';
      const block = [
        '',
        '**카드 ' + cardNo + ' · ' + title + '**',
        '  - 훅: ' + summary[0],
        '  - 맥락: ' + entry.topic + ' 흐름에서 `' + evidence + '` 신호가 포착됨',
        '  - 핵심 변화: ' + summary[1],
        '  - 왜 중요한가: ' + summary[2]
      ];
      if (item.url) {
        block.push('  - 근거/출처: [' + title.replace(/]/g, '') + '](' + escapeMarkdownUrl_(item.url) + ')');
      } else {
        block.push('  - 근거/출처: 메일 본문 내 공개 외부 링크 없음');
      }
      block.push('  - 시사점: compact 카드로 저장하고 원문에서 방법·평가·적용 범위를 확인');
      block.push('  - CTA/저장 포인트: ' + entry.topic + ' 후속 읽기 후보로 저장');
      block.push('  - 수집 메타: ' + sourceMeta);
      if (appendWithinLimit_(lines, block, BRIEFING_RENDER_CHAR_LIMIT)) {
        renderedItems += 1;
        renderedTopicCounts[entry.topic] = (renderedTopicCounts[entry.topic] || 0) + 1;
      } else {
        renderTruncated = true;
        cardNo -= 1;
      }
    });
    const remaining = entry.detailed.length - itemsToRender.length;
    if (remaining > 0) {
      appendWithinLimit_(lines, ['- raw archive 추가 보존: ' + remaining + '개'], BRIEFING_RENDER_CHAR_LIMIT);
    }
  });

  if (topics.length > selectedTopics.length) {
    renderTruncated = true;
    appendWithinLimit_(lines, ['- 추가 토픽: ' + (topics.length - selectedTopics.length) + '개는 다음 실행에서 재검토'], BRIEFING_RENDER_CHAR_LIMIT);
  }

  appendWithinLimit_(lines, ['', '━━━━━━━━━━━━━━━━━━━━', '운영 메모: 카드뉴스는 Discord Markdown/캐러셀 초안용 구조이며, 원문 메일 본문은 Discord에 게시하지 않습니다.'], BRIEFING_RENDER_CHAR_LIMIT);
  return {
    briefing: lines.join('\n'),
    telemetry: buildBriefingTelemetry_(items, query, selectedTopics, renderedItems, renderTruncated, renderedTopicCounts)
  };
}

function appendWithinLimit_(lines, block, limit) {
  const next = lines.concat(block);
  if (next.join('\n').length > limit) return false;
  block.forEach(line => lines.push(line));
  return true;
}


function buildBriefingTelemetry_(items, query, selectedTopics, renderedItems, renderTruncated, renderedTopicCounts) {
  const topicCounts = {};
  const topicDetails = {};
  const runId = Utilities.formatDate(new Date(), 'Etc/UTC', "yyyyMMdd'T'HHmmss'Z'");
  renderedTopicCounts = renderedTopicCounts || {};
  items.forEach(item => {
    const topic = item.topic || '기타 테크 리포트';
    topicCounts[topic] = (topicCounts[topic] || 0) + 1;
  });
  const detailedItemCount = selectedTopics.reduce((sum, entry) => sum + entry.detailed.length, 0);
  selectedTopics.forEach(entry => {
    topicDetails[entry.topic] = {
      total: entry.total,
      detailed: entry.detailed.length,
      rendered: renderedTopicCounts[entry.topic] || 0
    };
  });
  const maxRenderedInTopic = Object.keys(renderedTopicCounts).reduce((max, topic) => Math.max(max, renderedTopicCounts[topic] || 0), 0);
  const maxRenderedTopicShare = renderedItems > 0 ? maxRenderedInTopic / renderedItems : 0;
  return {
    run_id: runId,
    generated_at: new Date().toISOString(),
    query: query,
    item_count: items.length,
    url_candidate_count: items.filter(item => Boolean(item.url)).length,
    topic_count: Object.keys(topicCounts).length,
    rendered_topic_count: selectedTopics.length,
    rendered_item_count: renderedItems,
    detailed_item_count: detailedItemCount,
    max_topics: BRIEFING_MAX_TOPICS,
    max_items_per_topic: BRIEFING_MAX_ITEMS_PER_TOPIC,
    max_topic_share_target: BRIEFING_MAX_TOPIC_SHARE,
    max_rendered_topic_share: maxRenderedTopicShare,
    truncated: Boolean(renderTruncated),
    topic_counts: topicCounts,
    topic_details: topicDetails
  };
}

function saveLatestBriefing_(briefing, telemetry) {
  const generatedAt = telemetry && telemetry.generated_at ? telemetry.generated_at : new Date().toISOString();
  const itemCount = telemetry && typeof telemetry.item_count !== 'undefined' ? telemetry.item_count : 0;
  const query = telemetry && telemetry.query ? telemetry.query : '';
  PropertiesService.getScriptProperties().setProperties({
    LATEST_BRIEFING: briefing,
    LATEST_BRIEFING_AT: generatedAt,
    LATEST_BRIEFING_ITEM_COUNT: String(itemCount),
    LATEST_BRIEFING_QUERY: query,
    LATEST_BRIEFING_TELEMETRY: JSON.stringify(telemetry || {})
  });
}

function readLatestBriefingTelemetry_(props) {
  const raw = props.getProperty('LATEST_BRIEFING_TELEMETRY') || '';
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (e) {
    return { parse_error: true };
  }
}

function sanitizeRelayItems_(items) {
  return items.map(item => {
    const summaryLines = buildSummaryLines_(item);
    return {
      title: item.title || '',
      url: item.url || '',
      kind: item.kind || '',
      sender: item.sender || '',
      receivedAt: item.receivedAt || '',
      topic: item.topic || '',
      snippet: item.snippet || '',
      articleTitle: item.articleTitle || '',
      articleDescription: item.articleDescription || '',
      articleText: truncate_(plain_(item.articleText || ''), MAX_ARTICLE_CHARS),
      summaryLines: summaryLines,
      hasPublicArticleText: Boolean(item.articleText)
    };
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
  if (e && e.parameter && String(e.parameter.refresh || '') === 'true') {
    const payload = buildNewsletterArchivePayload_();
    if (String(e.parameter.include_items || '') === 'true') {
      return ContentService
        .createTextOutput(JSON.stringify(payload))
        .setMimeType(ContentService.MimeType.JSON);
    }
  }
  return ContentService
    .createTextOutput(JSON.stringify({
      briefing: props.getProperty('LATEST_BRIEFING') || '',
      generated_at: props.getProperty('LATEST_BRIEFING_AT') || '',
      item_count: Number(props.getProperty('LATEST_BRIEFING_ITEM_COUNT') || '0'),
      query: props.getProperty('LATEST_BRIEFING_QUERY') || '',
      telemetry: readLatestBriefingTelemetry_(props)
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
  ['unsubscribe', 'preferences', 'privacy', 'terms', 'login', 'signin', 'signup', 'account', 'settings', 'share', 'help', 'support.google.com', 'myaccount.google.com', 'facebook.com', 'twitter.com', 'x.com/', 'instagram.com'].forEach(p => { if (lower.indexOf(p) !== -1) score -= 20; });
  if (/\.(png|jpg|jpeg|gif|webp|svg|css|js)(\?|$)/i.test(lower)) score -= 30;
  return score;
}

function sanitizeUrl_(url) {
  return canonicalizeNewsletterUrl_(String(url || '').replace(/[.,;:!?)]}>'"]+$/g, ''));
}

function emptyArticleDetail_() {
  return { title: '', description: '', text: '' };
}

function fetchArticleText_(url) {
  return fetchArticleDetail_(url).text;
}

function fetchArticleDetail_(url) {
  const fetchUrl = normalizeArticleFetchUrl_(url);
  if (!isFetchableArticleUrl_(fetchUrl)) return emptyArticleDetail_();
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
    if (status < 200 || status >= 300) return emptyArticleDetail_();
    const headers = response.getAllHeaders ? response.getAllHeaders() : {};
    const contentType = String(headers['Content-Type'] || headers['content-type'] || '').toLowerCase();
    if (contentType && contentType.indexOf('text/html') === -1 && contentType.indexOf('text/plain') === -1) return emptyArticleDetail_();
    return extractArticleDetail_(response.getContentText());
  } catch (e) {
    return emptyArticleDetail_();
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
    'support.google.com',
    'myaccount.google.com',
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
  return extractArticleDetail_(html).text;
}

function extractArticleDetail_(html) {
  const raw = String(html || '');
  const meta = extractMetaDescription_(raw);
  const title = extractMetaTitle_(raw);
  const jsonLd = extractJsonLdArticleBody_(raw);
  const candidates = [
    jsonLd,
    extractTaggedContent_(raw, 'article'),
    extractTaggedContent_(raw, 'main'),
    meta,
    extractReadableText_(raw)
  ].map(plain_).filter(Boolean);
  const best = candidates.sort((a, b) => b.length - a.length)[0] || '';
  const parts = [];
  [meta, best].forEach(part => {
    if (part && parts.join(' ').indexOf(part) === -1) parts.push(part);
  });
  return {
    title: truncate_(cleanSummarySentence_(title), 120),
    description: truncate_(cleanSummarySentence_(meta), 240),
    text: removeTitleDuplicateSentences_(articleSentences_(parts.join('. ')), title).join('. ').slice(0, MAX_ARTICLE_CHARS)
  };
}

function removeTitleDuplicateSentences_(sentences, title) {
  const titleKey = normalizeSentenceKey_(title || '');
  if (!titleKey) return sentences;
  return sentences.filter(sentence => {
    const key = normalizeSentenceKey_(sentence);
    if (key === titleKey) return false;
    if (key === (titleKey + ' ' + titleKey).slice(0, key.length)) return false;
    return true;
  });
}

function extractJsonLdArticleBody_(html) {
  const blocks = String(html || '').match(/<script[^>]+type=["']application\/ld\+json["'][^>]*>[\s\S]*?<\/script>/gi) || [];
  for (let i = 0; i < blocks.length; i++) {
    const json = blocks[i].replace(/<script[^>]*>/i, '').replace(/<\/script>/i, '').trim();
    const fields = [];
    ['headline', 'name', 'description', 'abstract', 'articleBody'].forEach(field => {
      const re = new RegExp('\"' + field + '\"\\s*:\\s*\"([\\s\\S]*?)\"\\s*[,}]', 'i');
      const match = json.match(re);
      if (match) fields.push(decodeJsonString_(match[1]));
    });
    if (fields.length > 0) return fields.join('. ');
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
    .map(item => Object.assign({}, item, { summaryLines: buildSummaryLines_(item) }))
    .filter(item => item.summaryLines.length === 3);
}

function hasArticleDetail_(item) {
  return buildSummaryLines_(item).length === 3;
}

function buildSummaryLines_(item) {
  if (item.summaryLines && item.summaryLines.length === 3) return item.summaryLines;
  const publicText = joinUniqueText_([item.articleDescription, item.articleText]);
  const usablePublicText = publicText.length >= MIN_ARTICLE_TEXT_CHARS ? publicText : '';
  const displayTitle = item.articleTitle || item.title || '';
  const selected = selectRoleSummaryLines_(usablePublicText, item.topic || '', displayTitle, item.url || '', Boolean(usablePublicText));
  if (selected.length === 3) return selected.map(s => truncate_(cleanSummarySentence_(s), 190));
  return [];
}

function selectRoleSummaryLines_(text, topic, title, url, hasPublicArticleText) {
  const sentences = articleSentences_(text);
  if (sentences.length < 3) return buildEvidenceFallbackSummaryLines_(text, topic, title, url, sentences);
  const roles = [
    { name: 'core', terms: ['announce', 'announces', 'launch', 'launches', 'release', 'releases', 'introduce', 'introduces', 'propose', 'proposes', 'present', 'presents', 'new', 'first', '핵심', '공개', '출시', '발표', '제안'] },
    { name: 'technical', terms: ['architecture', 'framework', 'model', 'dataset', 'training', 'inference', 'retrieval', 'rag', 'agent', 'benchmark', 'evaluation', 'github', 'open-source', 'method', '방법', '모델', '데이터셋', '프레임워크', '아키텍처', '평가', '벤치마크', '검색', '추론'] },
    { name: 'impact', terms: ['improve', 'improves', 'outperform', 'outperforms', 'reduce', 'reduces', 'increase', 'increases', 'performance', 'accuracy', 'latency', 'cost', 'result', 'results', 'impact', '성능', '개선', '결과', '정확도', '비용', '지연', '효과', '한계'] }
  ];
  const picked = [];
  roles.forEach(role => {
    const candidate = bestSentenceForRole_(sentences, role, picked, topic, title, url, hasPublicArticleText);
    if (candidate) picked.push(candidate);
  });
  if (picked.length < 3) {
    const fallback = selectSubstantiveSentences_(text, topic, title, url, hasPublicArticleText);
    fallback.forEach(sentence => {
      if (picked.length < 3 && picked.indexOf(sentence) === -1) picked.push(sentence);
    });
  }
  return picked.length === 3 ? picked : [];
}

function buildEvidenceFallbackSummaryLines_(text, topic, title, url, sentences) {
  const evidence = cleanSummarySentence_(text);
  if (evidence.length < MIN_ARTICLE_TEXT_CHARS || sentences.length === 0) return [];
  const first = sentences[0];
  const technical = bestKeywordSentence_(sentences, ['model', 'dataset', 'training', 'inference', 'retrieval', 'rag', 'agent', 'benchmark', 'evaluation', 'architecture', 'framework', 'github', '모델', '데이터셋', '추론', '검색', '평가', '벤치마크']) || first;
  const impact = bestKeywordSentence_(sentences, ['improve', 'outperform', 'performance', 'accuracy', 'latency', 'cost', 'result', 'results', 'reduce', 'increase', '성능', '개선', '정확도', '비용', '결과', '한계']) || sentences[Math.min(1, sentences.length - 1)] || first;
  const host = publicHostLabel_(url);
  const titleHint = cleanSummarySentence_(title);
  const lines = [
    first,
    technical !== first ? technical : (host + ' 공개 원문에서 ' + summarizeTechnicalTerms_(evidence, topic) + ' 관련 기술 신호가 확인됩니다.'),
    impact !== first && impact !== technical ? impact : ((titleHint || host) + '의 공개 원문이 위 기술 신호를 후속 검증할 근거를 제공합니다.')
  ];
  return lines.length === 3 ? lines : [];
}

function bestKeywordSentence_(sentences, keywords) {
  let best = '';
  let bestScore = 0;
  sentences.forEach(sentence => {
    const lower = sentence.toLowerCase();
    let score = 0;
    keywords.forEach(keyword => { if (lower.indexOf(keyword.toLowerCase()) !== -1) score += 1; });
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

function bestSentenceForRole_(sentences, role, picked, topic, title, url, hasPublicArticleText) {
  let best = null;
  let bestScore = -999;
  sentences.forEach((sentence, idx) => {
    if (picked.indexOf(sentence) !== -1) return;
    let score = scoreSentence_(sentence, idx, topic, title, url, hasPublicArticleText);
    const lower = sentence.toLowerCase();
    role.terms.forEach(term => { if (lower.indexOf(term) !== -1) score += 6; });
    if (score > bestScore) {
      best = sentence;
      bestScore = score;
    }
  });
  return bestScore > 0 ? best : null;
}

function selectSubstantiveSentences_(text, topic, title, url, hasPublicArticleText) {
  const sentences = articleSentences_(text);
  const seen = {};
  const scored = [];
  sentences.forEach((sentence, idx) => {
    const normalized = normalizeSentenceKey_(sentence);
    if (seen[normalized]) return;
    seen[normalized] = true;
    const score = scoreSentence_(sentence, idx, topic, title, url, hasPublicArticleText);
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

function scoreSentence_(sentence, idx, topic, title, url, hasPublicArticleText) {
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
    'paper', 'study', 'experiment', 'method', 'pipeline', 'tool', 'repository', 'code', 'api', 'data', 'baseline', 'metric',
    '제안', '개선', '성능', '평가', '모델', '데이터셋', '프레임워크', '아키텍처', '검색', '그래프', '추론', '에이전트', '벤치마크', '논문', '실험', '방법론'
  ];
  strongTerms.forEach(term => { if (lower.indexOf(term) !== -1) score += 3; });
  const titleTerms = plain_(title).toLowerCase().split(/\s+/).filter(w => w.length >= 5).slice(0, 8);
  titleTerms.forEach(term => { if (lower.indexOf(term) !== -1) score += 1.5; });
  const topicTerms = String(topic || '').toLowerCase().split(/[\/\s·]+/).filter(Boolean);
  topicTerms.forEach(term => { if (lower.indexOf(term) !== -1) score += 1.5; });
  if (/\d+(\.\d+)?\s*(%|x|×|k|m|b|ms|s|tokens?|parameters?|benchmarks?|datasets?)/i.test(text)) score += 3;
  if (/[가-힣]/.test(text) && /(이다|한다|했다|제안|분석|개선|가능|필요|중요)/.test(text)) score += 2;
  const host = extractHost_(url);
  if (host && lower.indexOf(host.replace(/^www\./, '').split('.')[0]) !== -1) score += 1;
  if (hasPublicArticleText) score += 2;
  if (/\b(i|we|our|you|your)\b/i.test(text)) score -= 1;
  return score;
}

function articleSentences_(text) {
  return String(text || '')
    .replace(/([.!?。])\s*(?=[A-Z가-힣0-9])/g, '$1\n')
    .replace(/\s[-•*]\s+/g, '\n')
    .split(/\n+|[。]\s+/)
    .map(cleanSummarySentence_)
    .filter(s => s.length >= MIN_CONTEXT_SENTENCE_CHARS && !isBoilerplateSentence_(s));
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
  if (/^(table of contents|in this issue|this week|daily digest|weekly roundup)/i.test(lower)) return true;
  return false;
}

function extractTechnicalHint_(text) {
  const lower = String(text || '').toLowerCase();
  const hints = ['agent', 'rag', 'retrieval', 'llm', 'multimodal', 'benchmark', 'inference', 'open source', 'github', 'evaluation', 'safety', 'product', 'dataset', 'architecture', 'model'];
  const hit = hints.find(h => lower.indexOf(h) !== -1);
  return hit ? '`' + hit + '` 신호 확인' : '';
}


function canonicalizeNewsletterUrl_(url) {
  let text = String(url || '').trim();
  const wrapperMatch = text.match(/[?&](?:url|u|target|redirect|redirect_url|destination)=([^&#]+)/i);
  if (wrapperMatch) {
    try {
      const decoded = decodeURIComponent(wrapperMatch[1]);
      if (/^https?:\/\//i.test(decoded)) text = decoded;
    } catch (e) {
      // Keep original URL when decoding fails.
    }
  }
  text = text.replace(/[?&](?:utm_[^=&]+|fbclid|gclid|mc_cid|mc_eid|igshid|ref)=[^&#]*/gi, '');
  text = text.replace(/[?&]$/, '');
  return text;
}

function escapeMarkdownUrl_(url) {
  return String(url || '').replace(/\)/g, '%29').replace(/\(/g, '%28');
}

function normalizeArticleFetchUrl_(url) {
  const text = sanitizeUrl_(url);
  const arxivPdf = text.match(/^https?:\/\/(?:www\.)?arxiv\.org\/pdf\/([^?#]+)(?:\.pdf)?/i);
  if (arxivPdf) return 'https://arxiv.org/abs/' + arxivPdf[1].replace(/\.pdf$/i, '');
  return text;
}

function extractMetaTitle_(html) {
  const text = String(html || '');
  const patterns = [
    /<meta[^>]+property=["']og:title["'][^>]+content=["']([\s\S]*?)["'][^>]*>/i,
    /<meta[^>]+name=["']twitter:title["'][^>]+content=["']([\s\S]*?)["'][^>]*>/i,
    /<meta[^>]+content=["']([\s\S]*?)["'][^>]+property=["']og:title["'][^>]*>/i,
    /<title[^>]*>([\s\S]*?)<\/title>/i,
    /<h1[^>]*>([\s\S]*?)<\/h1>/i
  ];
  for (let i = 0; i < patterns.length; i++) {
    const match = text.match(patterns[i]);
    if (match) return decodeEntities_(extractReadableText_(match[1]));
  }
  return '';
}

function extractUrlContext_(body, url) {
  const text = plain_(body);
  const cleanUrl = sanitizeUrl_(url);
  if (!text || !cleanUrl) return '';
  const idx = text.indexOf(cleanUrl);
  if (idx === -1) return '';
  const start = Math.max(0, idx - 420);
  const end = Math.min(text.length, idx + cleanUrl.length + 420);
  return text.slice(start, end).replace(cleanUrl, ' ');
}

function extractHost_(url) {
  const match = String(url || '').match(/^https?:\/\/([^\/]+)/i);
  return match ? match[1].toLowerCase() : '';
}

function joinUniqueText_(parts) {
  const out = [];
  parts.forEach(part => {
    const clean = cleanSummarySentence_(part || '');
    if (!clean) return;
    if (out.join(' ').indexOf(clean) === -1) out.push(clean);
  });
  return out.join('. ');
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
    'support.google.com',
    'google.com/analytics/answer',
    'unsubscribe',
    'preferences',
    'privacy',
    'terms'
  ];
  return blocked.some(token => lower.indexOf(token) !== -1);
}

function isBlockedNewsletterItem_(subject, url, articleDetail, context) {
  const title = String((articleDetail && articleDetail.title) || subject || '').toLowerCase();
  const description = String((articleDetail && articleDetail.description) || '').toLowerCase();
  const lowerUrl = String(url || '').toLowerCase();
  const lowerContext = String(context || '').toLowerCase();
  if (isJobRelatedItem_(subject, url, description, context)) return true;
  if (isNonTechnicalNotificationItem_(subject, url, description, context)) return true;
  if (lowerUrl.indexOf('colab.research.google.com') !== -1) return true;
  if (title.indexOf('google colab') !== -1 || title === 'colab') return true;
  if (title.indexOf('colaboratory') !== -1) return true;
  if (lowerUrl.indexOf('c.gle/') !== -1 && (title.indexOf('colab') !== -1 || description.indexOf('colab') !== -1 || lowerContext.indexOf('colab') !== -1)) return true;
  return false;
}

function isNonTechnicalNotificationItem_(subject, url, description, context) {
  const lowerUrl = String(url || '').toLowerCase();
  const text = [
    subject || '',
    description || '',
    context || ''
  ].join(' ').toLowerCase();
  const isLinkedIn = lowerUrl.indexOf('linkedin.com') !== -1 || text.indexOf('linkedin') !== -1;
  if (!isLinkedIn) return false;
  const urlHints = [
    'linkedin.com/analytics',
    'linkedin.com/notifications',
    'linkedin.com/comm/notifications',
    'linkedin.com/comm/feed/update'
  ];
  if (urlHints.some(token => lowerUrl.indexOf(token) !== -1)) return true;
  const textHints = [
    '업데이트의 지난 주 노출수',
    '지난 주 노출수',
    '노출수',
    '프로필 조회',
    '게시물 조회',
    '회원님의 업데이트',
    '회원님의 게시물',
    '님 업데이트',
    '님 게시물',
    'impressions',
    'profile views',
    'post views',
    'people viewed your profile',
    'your update',
    'your post',
    'weekly stats',
    'analytics'
  ];
  return textHints.some(token => text.indexOf(token) !== -1);
}

function isJobRelatedItem_(subject, url, description, context) {
  const lowerUrl = String(url || '').toLowerCase();
  const text = [
    subject || '',
    description || '',
    context || ''
  ].join(' ').toLowerCase();
  const jobUrlHints = [
    'linkedin.com/jobs',
    'linkedin.com/comm/jobs',
    'linkedin.com/jobs/view',
    'linkedin.com/job-collections'
  ];
  if (jobUrlHints.some(token => lowerUrl.indexOf(token) !== -1)) return true;
  if (lowerUrl.indexOf('linkedin.com') !== -1) {
    const jobTextHints = ['job alert', 'job recommendation', 'hiring', '채용', '채용공고', '구인', '지원하기'];
    if (jobTextHints.some(token => text.indexOf(token) !== -1)) return true;
  }
  return false;
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
  let best = null;
  TOPIC_RULES.forEach(rule => {
    const score = scoreTopicRule_(lower, rule);
    if (score < TOPIC_SCORE_THRESHOLD) return;
    if (!best || score > best.score || (score === best.score && rule.priority < best.priority)) {
      best = { label: rule.label, score: score, priority: rule.priority };
    }
  });
  if (best) {
    return best.label;
  }
  if (lower.indexOf('arxiv.org') !== -1 || lower.indexOf('doi.org') !== -1 || lower.indexOf('openreview.net') !== -1) return '논문/리서치';
  return '기타 테크 리포트';
}

function scoreTopicRule_(lower, rule) {
  let score = 0;
  (rule.phrases || []).forEach(phrase => {
    if (containsTokenPhrase_(lower, phrase)) score += 4;
  });
  (rule.terms || []).forEach(term => {
    if (containsTokenPhrase_(lower, term)) score += 2;
  });
  (rule.substrings || []).forEach(token => {
    if (lower.indexOf(token) !== -1) score += 2;
  });
  return score;
}

function containsTokenPhrase_(lower, phrase) {
  const escaped = String(phrase || '').toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s+');
  if (!escaped) return false;
  return new RegExp('(^|[^a-z0-9])' + escaped + '([^a-z0-9]|$)').test(lower);
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
