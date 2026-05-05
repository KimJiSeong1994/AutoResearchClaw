# Newsletter blog-post cardnews template

## Purpose

`newsletter_ingest.py`, the Apps Script fallback, and the Discord bridge should
produce a blog-post-shaped publication first, then derive compact Discord/card
blocks from that article. The output must read as one Korean technical briefing
with a problem statement, argument, interpretation, questions, reuse blocks, and
sources instead of a flat link list.

## Blog publication contract

Every rendered publication starts with:

1. **대표 이미지 설명** — safe abstract hero-image prompt/description, no logos,
   readable text, private data, or living-person likeness.
2. **3줄 요약** — change, industry/field meaning, remaining question.
3. **왜 지금 이 이슈인가** — timing and affected technical/organizational context.
4. **핵심 주장** — claim, evidence level, and application scene.
5. **논증 구조** — observation, mechanism, tension, counter-reading, judgment.
6. **산업사회학적·현장기반 해석** — incentives, labor/organization/platform or
   research-ecosystem implications.
7. **앞으로 볼 질문** — what to verify next.
8. **카드뉴스 재사용안** and **디스코드 브리핑 재사용안** — short derivative blocks.
9. **출처** — public URLs only.

## Card reuse arc

Cards remain reusable, but they are now downstream excerpts from the blog-shaped
briefing:

1. **훅** — why this link deserves a swipe/open.
2. **맥락** — topic lane and detected technical signal.
3. **핵심 변화** — what changed technically.
4. **왜 중요한가** — researcher/operator relevance.
5. **근거/출처** — public URL only.
6. **시사점** — compact taxonomy/confidence or action implication.
7. **CTA/저장 포인트** — what to save or inspect next.

## Runtime parity

- Python renderer: `skills/paper-recommender/newsletter_ingest.py::render_topic_briefing`.
- Apps Script renderer: `integrations/google-apps-script/newsletter_archive_to_discord.gs::renderBriefingWithTelemetry_`.
- Discord card bridge: `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_card_news.py::render_card_news_messages`.

Python and Apps Script intentionally share the Korean blog section labels above
plus the card labels. The Discord bridge uses the same article header and then
posts narrative card messages from the raw newsletter archive.

## Privacy boundary

Do not render or persist full email bodies, OAuth tokens, Script Properties,
webhook URLs, relay tokens, private workspace URLs, or raw paid-newsletter text.
Private context can only influence deterministic classification through
sanitized metadata/digests; final briefings must contain public excerpts, public
URLs, sender/date metadata, and classification labels only.
