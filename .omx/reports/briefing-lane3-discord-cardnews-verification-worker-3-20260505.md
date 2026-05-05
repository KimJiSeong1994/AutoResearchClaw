# Lane3 Discord card-news renderer verification — worker-3 — 2026-05-05

## Scope

Review and document the Discord bridge card-news renderer for the 기술 브리핑 카드뉴스 restructure. This lane covers `skills/discord-openclaw-bridge/` only, with Python newsletter renderer and Apps Script parity findings reported as integration blockers when discovered by probes.

## Current Discord bridge contract

- Renderer: `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/post_card_news.py`
- Entrypoint: `discord-openclaw-post-card-news`
- Wrapper: `skills/discord-openclaw-bridge/project/scripts/post-card-news.sh`
- Source archive: latest `NEWSLETTER_WIKI_ROOT/raw/newsletters/*/items.json` unless `DISCORD_CARD_NEWS_SOURCE` is set.
- Output shape: header card, topic-spread item cards, rich/lean/skeletal card variants, suppressed Discord embeds.
- Privacy boundary: render from sanitized archive fields only; do not post raw Gmail bodies, OAuth tokens, Script Properties, webhook URLs, relay tokens, Discord bot token, or OpenClaw gateway secrets.

## Verification evidence

PASS — Discord bridge tests:

```bash
cd skills/discord-openclaw-bridge/project
uv run --with pytest pytest -q
# 32 passed in 0.10s
```

PASS — Discord bridge lint:

```bash
cd skills/discord-openclaw-bridge/project
uv run --with ruff ruff check src tests
# All checks passed!
```

PASS — Discord bridge compile/type-equivalent check:

```bash
cd skills/discord-openclaw-bridge/project
python3 -m compileall -q src
# passed
```

PASS — posting wrapper syntax:

```bash
cd skills/discord-openclaw-bridge/project
bash -n scripts/post-card-news.sh scripts/post-newsletter-briefing.sh scripts/post-briefing.sh
# passed
```

PASS — Apps Script syntax smoke:

```bash
tmp=$(mktemp /tmp/newsletter_archive_to_discord.XXXXXX).js
cp integrations/google-apps-script/newsletter_archive_to_discord.gs "$tmp"
node --check "$tmp"
rm -f "$tmp"
# passed
```

FAIL — cross-lane Python newsletter renderer tests currently fail outside Lane3:

```bash
cd skills/paper-recommender/project
uv run --with pytest --with pyyaml --with httpx python -m pytest tests/test_newsletter_ingest.py tests/test_gmail_newsletter_briefing.py -q
# 6 failed, 22 passed
```

Observed blocker from review probe: `skills/paper-recommender/newsletter_ingest.py` references undefined `kind`, `sender`, and `received` in `render_topic_briefing(...)`. This blocks full Python/GAS newsletter-path acceptance but is outside the Discord bridge edit lane.

## Probe findings integrated

Subagents spawned: 2

- Review probe (`019df6d7-e2e7-7ba3-9343-deb512c8540f`): identified Python renderer NameError, GAS snippet privacy risk, GAS direct-render parity drift, URL filtering gaps, Discord max-length/purge/hero-image risks, and missing Lane3 checklist.
- Test probe (`019df6d7-f47c-78f2-a917-0c206e8eedbf`): mapped existing Python/GAS/Discord tests and recommended documenting exact Lane3 pytest/lint/compile/script checks.

Integrated changes:

- Added a `skills/discord-openclaw-bridge/README.md` card-news publishing section with command, env controls, privacy boundary, and Lane3 verification checklist.
- Left Python/GAS implementation defects as reported blockers for Lane1/Lane2 owners rather than widening this lane's write scope.

## Remaining risks / handoff

- Python newsletter renderer must be fixed before claiming full newsletter/cardnews path acceptance.
- Apps Script should either render the same compact card-news contract or document direct GAS rendering as legacy with relay-to-Python as canonical.
- Add future Discord bridge hardening for max message size, safer purge defaults, and hero-image validation if the leader widens Lane3 from documentation/review into behavior changes.
