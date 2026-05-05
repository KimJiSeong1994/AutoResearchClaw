# Newsletter briefing cardnews template

## Purpose

`newsletter_ingest.py`, the Apps Script fallback, and the Discord bridge should
produce the same publication shape: compact Markdown cards that can be copied to
Discord today and reused as a carousel/storyboard later.

## Card arc

Each rendered card keeps one article/paper as one message block:

1. **훅** — why this link deserves a swipe/open.
2. **맥락** — topic lane and detected technical signal.
3. **핵심 변화** — what changed technically.
4. **왜 중요한가** — researcher/operator relevance.
5. **근거/출처** — public URL only.
6. **시사점** — compact taxonomy/confidence or action implication.
7. **CTA/저장 포인트** — what to save or inspect next.

This follows the web-research synthesis in
`.omx/context/cardnews-briefing-template-20260505T063200Z.md`: carousel/cardnews
formats work best with a hook-first narrative, one idea per card, mobile-first
compact copy, evidence, and a save/share CTA.

## Runtime parity

- Python renderer: `skills/paper-recommender/newsletter_ingest.py::render_topic_briefing`.
- Apps Script renderer: `integrations/google-apps-script/newsletter_archive_to_discord.gs::renderBriefingWithTelemetry_`.
- Discord card bridge: `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_card_news.py::render_card_news_messages`.

Python and Apps Script intentionally share the Korean section labels above. The
Discord bridge already emits richer per-card narrative frames from the raw
newsletter archive; use `post_card_news.py` when the raw payload has public
article summaries and topic metadata.

## Privacy boundary

Do not render or persist full email bodies, OAuth tokens, Script Properties,
webhook URLs, relay tokens, or private workspace URLs. Private context can only
influence deterministic classification through sanitized metadata/digests; final
briefings must contain public excerpts, public URLs, sender/date metadata, and
classification labels only.
