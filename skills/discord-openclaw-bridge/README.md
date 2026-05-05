# Discord OpenClaw Bridge

Minimal Discord bot bridge for operating OpenClaw from one Discord channel.

Default target:

- Guild/server: `1500743272551813142`
- Channel: `1500743273361440823`
- OpenClaw gateway: `http://127.0.0.1:18789/v1`

The bridge runs on the EC2 OpenClaw host and calls the OpenClaw gateway over loopback only. It exposes:

- `/openclaw prompt:<text>` — ask OpenClaw from the allowlisted channel
- `/openclaw_status` — lightweight OpenClaw health check
- `/jiphyeonjeon_briefing` — post the latest Jiphyeonjeon-Claw AI briefing from `DISCORD_BRIEFING_SOURCE`
- `/jiphyeonjeon_mine url:<link> [title] [note]` — register a Discord-requested link for 집현전-광부 intake and 집현전-클로 content review
- standalone `discord-jiphyeonjeon-miner.service` for running 집현전-광부 as an individual Discord bot with `DISCORD_MINER_BOT_TOKEN`
- one-shot newsletter/card-news publishers from the installed project scripts
- mention replies, only if `DISCORD_ENABLE_MENTION_RESPONSES=1`

## 집현전-광부 link-intake path

집현전-광부 is a collection-only sub-agent for Discord link requests. It does not decide newsletter inclusion. The content-review owner remains 집현전-클로.

Flow:

1. A user runs `/jiphyeonjeon_mine` in `DISCORD_MINER_CHANNEL_ID`, or a dedicated intake channel is enabled with `DISCORD_MINER_ENABLE_CHANNEL_COLLECTION=1`.
2. The bridge extracts and sanitizes HTTP(S) links, stripping secret/tracking query keys such as tokens and `utm_*`.
3. Pending records are appended to both:
   - `JIPHYEONJEON_MINER_INTAKE_PATH`
   - `JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH`
4. Each record is marked `status=pending_claw_review`, `agent=jiphyeonjeon-miner`, and `reviewer=jiphyeonjeon-claw`.
5. Only after 집현전-클로 approval should an operator or review automation copy approved metadata into the newsletter manual-link/archive ingestion path.

Operational controls:

- `DISCORD_MINER_CHANNEL_ID` defaults to `DISCORD_ALLOWED_CHANNEL_ID` when unset.
- `DISCORD_MINER_ENABLE_CHANNEL_COLLECTION` defaults to disabled. Enable it only for a dedicated intake channel because it requires Discord `MESSAGE_CONTENT` intent.
- For an individual Miner bot, set `DISCORD_MINER_BOT_TOKEN` and `DISCORD_MINER_CLIENT_ID`, invite it with `project/scripts/invite-miner-url.sh`, then install/start `discord-jiphyeonjeon-miner.service`.
- The stored Discord metadata is limited to guild/channel/message/user IDs. Full message bodies are not persisted.
- Do not point the paper-recommender `manual_links` source at the pending intake/review queue; use an approved-only JSONL file after 집현전-클로 review.

## Setup on EC2

```bash
cd ~/.openclaw/workspace/skills/discord-openclaw-bridge
cp project/.env.example project/.env
$EDITOR project/.env  # set DISCORD_BOT_TOKEN and optional DISCORD_CLIENT_ID
bash project/scripts/install.sh
systemctl --user start discord-openclaw-bridge.service
bash project/scripts/status.sh
```

To run 집현전-광부 as a separate Discord application/bot:

```bash
cd ~/.openclaw/workspace/skills/discord-openclaw-bridge
$EDITOR project/.env  # set DISCORD_MINER_BOT_TOKEN, DISCORD_MINER_CLIENT_ID, DISCORD_MINER_CHANNEL_ID
bash project/scripts/invite-miner-url.sh
bash project/scripts/install-miner.sh
systemctl --user start discord-jiphyeonjeon-miner.service
bash project/scripts/status.sh
```

To publish briefings immediately after the token is configured:

```bash
# Markdown briefing from DISCORD_BRIEFING_SOURCE.
bash project/scripts/post-briefing.sh

# Newsletter Markdown from DISCORD_NEWSLETTER_BRIEFING_SOURCE.
bash project/scripts/post-newsletter-briefing.sh

# Card-news carousel-style Markdown from newsletter raw archive items.json.
bash project/scripts/post-card-news.sh
```

To schedule the card-news publisher on the same EC2 cron cadence as the AI
newsletter archive publisher (23:00 UTC = 08:00 KST), install the idempotent
cron runner from the repository root:

```bash
bash skills/discord-openclaw-bridge/install-card-news-cron.sh
```

The scheduled runner starts on the same cron minute and waits briefly before
posting, so the newsletter archive job can finish writing the fresh
`raw/newsletters/YYYY-MM-DD/items.json` first.

## Card-news publishing path

`project/src/discord_openclaw_bridge/post_card_news.py` renders the newsletter raw archive into compact Discord messages shaped for a card-news/carousel read:

1. a header card with the publication date, cross-topic theme, and selected/collected counts;
2. up to `DISCORD_CARD_NEWS_MAX_CARDS` item cards selected for topic spread before duplicates;
3. rich cards as short narrative paragraphs (`why now` → claim/mechanism → evidence → optional next question), lean cards as public-excerpt notes, and skeletal cards as explicit follow-up candidates.

Operational controls:

- `DISCORD_CARD_NEWS_CHANNEL_ID` defaults to the card-news forum/channel `1501073491921993758`.
- `DISCORD_CARD_NEWS_SOURCE` defaults to the latest `NEWSLETTER_WIKI_ROOT/raw/newsletters/*/items.json` archive, preferring today.
- `DISCORD_CARD_NEWS_MAX_CARDS` defaults to `8` to keep the Discord thread compact.
- `DISCORD_CARD_NEWS_HERO_IMAGE_PATH` optionally attaches a PNG hero image when posting to a Discord forum channel.
- `DISCORD_PURGE_PREVIOUS_CARD_NEWS` defaults to enabled; previous bot-authored card-news posts/active threads are removed before reposting.

Privacy boundary: the renderer uses sanitized archive fields (`article_title`, `summary_lines`, `why_now`, `claim`, `mechanism`, `evidence`, public excerpt/description, source name, URL, topic labels/reasons). It does not read or post Gmail bodies, OAuth tokens, Script Properties, webhook URLs, relay tokens, or OpenClaw gateway secrets. Discord embeds are suppressed for posted messages.


## Lane3 verification checklist

Before posting to Discord, verify the local bridge path end-to-end without exposing secrets:

```bash
cd skills/discord-openclaw-bridge/project
uv run --with pytest --with httpx python -m pytest \
  tests/test_post_card_news.py \
  tests/test_post_newsletter.py \
  tests/test_briefing.py -q
uv run --with ruff ruff check src tests
python3 -m compileall -q src
bash -n scripts/post-card-news.sh scripts/post-newsletter-briefing.sh scripts/post-briefing.sh
```

Pre-publish checks:

- Confirm `DISCORD_BOT_TOKEN` is configured locally, but never print it in logs or tickets.
- Confirm `DISCORD_CARD_NEWS_SOURCE` points to a sanitized `items.json`; if unset, the script uses the latest archive under `NEWSLETTER_WIKI_ROOT/raw/newsletters/`.
- Inspect a rendered fixture with `discord_openclaw_bridge.post_card_news.render_card_news_messages(...)` and assert no raw Gmail body snippets, OAuth tokens, Script Properties, webhook URLs, relay tokens, or gateway tokens appear.
- Keep `DISCORD_CARD_NEWS_MAX_CARDS` small enough for a readable thread; default is `8`.
- Review `DISCORD_PURGE_PREVIOUS_CARD_NEWS` before production posting. It defaults to enabled for replacement posts and removes prior bot-authored card-news posts/active card-news forum threads.
- If using `DISCORD_CARD_NEWS_HERO_IMAGE_PATH`, use a non-secret PNG asset intended for public Discord posting.

Post-publish checks:

- Confirm the header card shows date, theme, and selected/collected counts.
- Confirm item cards keep the card-news narrative arc and suppress link embeds.
- Confirm Discord thread/channel contains no private email text or secret configuration values.

## Invite URL

```bash
bash project/scripts/invite-url.sh YOUR_DISCORD_CLIENT_ID
```

Install the app with scopes `bot applications.commands`, then restrict the app/bot to the target channel in Discord channel permissions or Server Settings → Integrations.

## Security defaults

- No `Administrator` permission requested by the invite helper.
- OpenClaw gateway remains loopback-only.
- Discord and OpenClaw tokens are read from local secret files/env only.
- Card-news output is generated from already-sanitized newsletter archive fields; do not add raw email bodies or secret env values to card text.
- Full prompts are not logged by default.
