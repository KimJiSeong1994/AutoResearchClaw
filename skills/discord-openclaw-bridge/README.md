# Discord OpenClaw Bridge

Minimal Discord bot bridge for operating OpenClaw from one Discord channel.

Default target:

- Guild/server: `<DISCORD_GUILD_ID>`
- Channel: `<DISCORD_ALLOWED_CHANNEL_ID>`
- OpenClaw gateway: `http://127.0.0.1:18789/v1`

The bridge runs on the EC2 OpenClaw host and calls the OpenClaw gateway over loopback only. It exposes:

- `/openclaw prompt:<text>` — ask OpenClaw from the allowlisted channel
- `/openclaw_status` — lightweight OpenClaw health check
- `/jiphyeonjeon_briefing` — post the latest Jiphyeonjeon-Claw AI briefing from `DISCORD_BRIEFING_SOURCE`
- standalone `discord-jiphyeonjeon-miner.service` for running 집현전-광부 as an individual Discord bot with `DISCORD_MINER_BOT_TOKEN`
- post-only 집현전-경비원 agent (`discord-openclaw-post-miner-seeds-report`) that publishes the daily miner-seeds run summary to the 운영리포팅 forum under its own bot identity when `DISCORD_GUARD_BOT_TOKEN` is provided
- one-shot newsletter/card-news publishers from the installed project scripts
- mention replies, only if `DISCORD_ENABLE_MENTION_RESPONSES=1`

The main OpenClaw bridge intentionally does not collect links or register
`/jiphyeonjeon_mine`; the dedicated Miner bot is the only application that
responds in `DISCORD_MINER_CHANNEL_ID`.

## 집현전-광부 link-intake path

집현전-광부 is a collection-only sub-agent for Discord link requests. It does not decide newsletter inclusion. The content-review owner remains 집현전-클로.

Flow:

1. A user runs the Miner app's `/jiphyeonjeon_mine` in `DISCORD_MINER_CHANNEL_ID`, or the dedicated intake channel is enabled with `DISCORD_MINER_ENABLE_CHANNEL_COLLECTION=1`.
2. The Miner bot extracts and sanitizes HTTP(S) links, stripping secret/tracking query keys such as tokens and `utm_*`.
3. Pending records are appended to both:
   - `JIPHYEONJEON_MINER_INTAKE_PATH`
   - `JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH`
4. Each record is marked `status=pending_claw_review`, `agent=jiphyeonjeon-miner`, and `reviewer=jiphyeonjeon-claw`.
5. 집현전-클로 records an append-only decision in `link-review-decisions.jsonl` with `approve`, `reject`, or `hold`.
6. The approved-only export `approved-manual-links.jsonl` remains an audit/reuse artifact, but Miner-collected links are not injected into the newsletter raw archive or card-news source; pending queue files are never newsletter inputs.

Deep build plan for the review workflow:

- Keep Miner intake collection-only: sanitize URL risk, append the original pending record to intake and review queue, and leave all inclusion decisions to 집현전-클로.
- Make JSONL writes repairable and locked: every append uses a sidecar lock and `fsync`; duplicate checks repair a missing intake or queue row instead of suppressing it.
- Store decisions as audit events: `discord-jiphyeonjeon-miner-review approve|reject|hold <intake_id>` appends to `link-review-decisions.jsonl` without mutating the pending queue.
- Export only after approval: `discord-jiphyeonjeon-miner-review export` joins the queue with latest decisions, enriches missing/fallback metadata from the public HTML page, and writes `approved-manual-links.jsonl` atomically for `manual_links` compatibility.
- Verify the boundary with tests before pointing downstream jobs at the export path.

Operator CLI:

```bash
cd skills/discord-openclaw-bridge/project
uv run discord-jiphyeonjeon-miner-review list
uv run discord-jiphyeonjeon-miner-review show miner_<id>
uv run discord-jiphyeonjeon-miner-review approve miner_<id> --reason "source checked"
uv run discord-jiphyeonjeon-miner-review reject miner_<id> --reason "off-topic or unsafe"
uv run discord-jiphyeonjeon-miner-review hold miner_<id> --reason "needs source verification"
uv run discord-jiphyeonjeon-miner-review export
# Deterministic/offline export without public page metadata fetch:
uv run discord-jiphyeonjeon-miner-review export --no-enrich
```

Default paths:

- Queue: `~/.openclaw/workspace/review/jiphyeonjeon-claw/link-review-queue.jsonl`
- Decision audit log: `~/.openclaw/workspace/review/jiphyeonjeon-claw/link-review-decisions.jsonl`
- Approved export: `~/.openclaw/workspace/manual_links/approved-manual-links.jsonl`

Operational controls:

- `DISCORD_MINER_CHANNEL_ID` defaults to `DISCORD_ALLOWED_CHANNEL_ID` when unset.
- `DISCORD_MINER_ENABLE_CHANNEL_COLLECTION` defaults to disabled. Enable it only for a dedicated intake channel because it requires the Miner bot's Discord `MESSAGE_CONTENT` intent.
- `JIPHYEONJEON_MINER_DECISIONS_PATH` and `JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH` control the review audit log and approved-only `manual_links` export path.
- For an individual Miner bot, set `DISCORD_MINER_BOT_TOKEN` and `DISCORD_MINER_CLIENT_ID`, invite it with `project/scripts/invite-miner-url.sh`, then install/start `discord-jiphyeonjeon-miner.service`.
- For an individual Traveler bot, set `DISCORD_TRAVELER_BOT_TOKEN` and `DISCORD_TRAVELER_CLIENT_ID`, invite it with `project/scripts/invite-traveler-url.sh`, then install/start `discord-jiphyeonjeon-traveler.service`.
- The main OpenClaw bot ignores normal messages in `DISCORD_MINER_CHANNEL_ID` and no longer registers the Miner intake command. If you also need to hide unrelated OpenClaw slash commands from the Miner channel UI, set that in Discord's integration command permissions; Discord rejects that endpoint for bot tokens.
- The stored Discord metadata is limited to guild/channel/message/user IDs. Full message bodies are not persisted.
- Do not point the paper-recommender `manual_links` source at the pending intake/review queue. Newsletter archive/card-news jobs count approved Miner links for exclusion evidence but do not merge them into the public newsletter surfaces.

Queue and publication-adjacent review helpers:

- `discord-openclaw-guard-ops-digest` remains an observability-only Guard surface. It may read the Traveler report status, Miner request message id, Miner intake, and review queue to report whether the daily Traveler→Miner handoff was confirmed, but it must not reorder queues, append Claw decisions, rewrite approved exports, or trigger newsletter/card-news publishing.
- `discord-openclaw-review-queue-optimizer` is report-only. It emits duplicate, stale, and priority recommendations with `no_mutation=true`; it never writes queue, decision, or approved export artifacts.
- `discord-openclaw-newsletter-candidate-orchestrator` reads Claw-approved manual links and creates a separate editorial candidate artifact whose rows start as `candidate_status=needs_editorial_review`. It does not write newsletter raw archives, card-news source files, or public Discord posts.

## 집현전-여행자 source-discovery path

집현전-여행자 is a research-only source discovery agent that works upstream of
집현전-광부. Its role is to find credible, recurring, and collection-friendly
public information sources — newsletters, article hubs, research lab blogs,
engineering blogs, conference/working-paper feeds, dataset/release feeds, and
curated technical indexes — that can become Miner seed candidates.

Traveler does not collect individual Discord links, approve newsletter
inclusion, or write to `approved-manual-links.jsonl`. It must perform deep
research over many possible sources before selecting candidates: a single URL
suggestion is only a research lead, not an accepted source. It proposes
source-level candidates for operator/집현전-클로 review, and only accepted
candidates should be handed to 집현전-광부 seed expansion.

Source reliability rubric:

1. **Credibility:** primary organization, named author/editorial process,
   visible provenance, or reputable technical/research venue.
2. **Continuity:** recurring publication cadence or stable feed/archive page
   that can be revisited without one-off scraping.
3. **Technical fit:** academic search, AI/ML systems, retrieval/RAG,
   agents, evaluation, multimodal, infrastructure, or other
   Jiphyeonjeon-Claw technical-report topics.
4. **Collection feasibility:** public HTTP(S), no login-only body, no private
   mailbox dependence, no hostile terms, and low risk of leaking secret query
   parameters.
5. **Yield signal:** recent posts contain concrete papers, technical reports,
   release notes, datasets, benchmarks, or engineering writeups rather than
   generic career, market, or promotional material.

Traveler-to-Miner handoff contract:

- Discord command: `/jiphyeonjeon_travel topic:<topic> scope:<optional>
  min_sources_to_review:<default 20> note:<optional>` records a deep-research
  request through the standalone Traveler bot. It does not directly add a
  source to Miner seeds.
- Research request queue path: `JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH`,
  defaulting operationally to
  `~/.openclaw/workspace/review/jiphyeonjeon-traveler/research-requests.jsonl`.
- Candidate queue path: `JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH`, defaulting
  operationally to `~/.openclaw/workspace/review/jiphyeonjeon-traveler/source-candidates.jsonl`.
- Each research request enforces `minimum_sources_to_review` (clamped to at
  least 10) and requires rejected-source evidence; no source can be fast-tracked
  from a single URL.
- Each candidate record should include source URL, source type, update cadence
  evidence, reliability rationale, topic fit, collection method hint
  (`rss`, `archive_page`, `newsletter_landing`, `manual_watch`, or `reject`),
  access constraints, and recommended next action.
- Traveler records are source recommendations only. They are not newsletter
  items and must not bypass 집현전-클로 review or 집현전-광부 URL sanitization.
- Rejected source classes: jobs/career-only pages, social/admin notifications,
  private or login-only newsletters, analytics dashboards, generic market
  commentary, and sources dominated by non-technical content.

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

To run 집현전-여행자 as a separate Discord application/bot:

```bash
cd ~/.openclaw/workspace/skills/discord-openclaw-bridge
$EDITOR project/.env  # set DISCORD_TRAVELER_BOT_TOKEN, DISCORD_TRAVELER_CLIENT_ID, DISCORD_TRAVELER_CHANNEL_ID
bash project/scripts/invite-traveler-url.sh
bash project/scripts/install-traveler.sh
systemctl --user start discord-jiphyeonjeon-traveler.service
bash project/scripts/status.sh
```

집현전-경비원 (Jiphyeonjeon-Guard) is the post-only agent that publishes the
miner-seeds daily run summary to the 운영리포팅 forum. It is not a
long-running service — `discord-openclaw-post-miner-seeds-report` is invoked
once per cron firing by `scripts/run-miner-seeds.sh`. To give it a dedicated
identity in the channel author column:

1. Create a new application in the Discord Developer Portal named
   `Jiphyeonjeon-Guard`, add a bot, copy the bot token.
2. Set `DISCORD_GUARD_BOT_TOKEN` and `DISCORD_GUARD_CLIENT_ID` in
   `project/.env` (mode 600).
3. Run `bash project/scripts/invite-guard-url.sh` and open the printed URL
   to invite the bot into the guild with view/send/forum-thread permissions.
4. Confirm the bot appears in the 운영리포팅 forum's permission list. The
   next miner-seeds cron firing will post under 집현전-경비원.

Without `DISCORD_GUARD_BOT_TOKEN` the report falls back to the main bridge
bot (still functional, just visually identified as the bridge instead of the
guard agent).

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

- `DISCORD_CARD_NEWS_CHANNEL_ID` defaults to the `ai-뉴스레타` forum/channel `<DISCORD_NEWSLETTER_CHANNEL_ID>`.
- `DISCORD_CARD_NEWS_SOURCE` defaults to the latest `NEWSLETTER_WIKI_ROOT/raw/newsletters/*/items.json` archive, preferring today.
- `DISCORD_CARD_NEWS_MAX_CARDS` defaults to `8` to keep the Discord thread compact.
- `DISCORD_CARD_NEWS_HERO_IMAGE_PATH` optionally attaches a PNG hero image when posting to a Discord forum channel.
- `DISCORD_PURGE_PREVIOUS_CARD_NEWS` defaults to enabled; previous bot-authored card-news posts/active threads are removed before reposting.
- `DISCORD_CARD_NEWS_QUALITY_GATE` defaults to enabled (`1`). Set it to `0` to roll back to the previous publish behavior without novelty/substance gating.
- `DISCORD_CARD_NEWS_AUDIT_PATH` optionally overrides the JSONL audit/history path. If unset, the path is `$NEWSLETTER_WIKI_ROOT/state/card-news-publication-audit.jsonl`, or `~/.openclaw/state/discord-openclaw-bridge/card-news-publication-audit.jsonl` when `NEWSLETTER_WIKI_ROOT` is unset.
- `DISCORD_CARD_NEWS_HISTORY_DAYS` defaults to `14`; only recent successful `decision=publish` audit records are used for previous-publication overlap.
- `DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS` defaults to `3`, `DISCORD_CARD_NEWS_MIN_NEW_CARDS` defaults to `3`, `DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS` defaults to `2`, and `DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO` defaults to `0.5`.

Quality gate behavior:

- The gate runs after archive load and optional public metadata enrichment, then evaluates the exact selected cards that will be rendered.
- A skip appends one sanitized audit record, prints `skipped card news quality_gate ...`, and exits before Discord channel lookup, purge, thread creation, or message posting. Existing Discord card-news is left untouched.
- A successful publish appends a sanitized `decision=publish` record with counts, thresholds, card fingerprints, message count, purge count, and thread ID when a forum thread is created.
- If a Discord side effect starts and later fails, the publisher best-effort appends `decision=failure` with the failed stage and sanitized counts before propagating the error.
- Audit records store public titles, sanitized URLs or URL hashes, topic labels, fingerprints, privacy-safe hashed content signatures, evidence kind/richness, counts, thresholds, and reason codes. Do not add raw Gmail bodies, secrets, tokens, webhook URLs, private env values, or unsanitized source paths to this audit.

Publication quality gate controls:

- `DISCORD_CARD_NEWS_QUALITY_GATE` defaults to enabled (`1`). Set it to `0` for the fastest rollback to the previous publish behavior; leave the audit file in place unless you are intentionally resetting history.
- `DISCORD_CARD_NEWS_HISTORY_DAYS` defaults to `14` and limits how far back the gate reads previous `decision=publish` audit records for novelty/overlap checks.
- `DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS` defaults to `3`; skeletal fallback cards do not count toward this threshold.
- `DISCORD_CARD_NEWS_MIN_NEW_CARDS` defaults to `3`; cards are considered new only when both their sanitized URL/story identity and their hashed content signature are absent from recent published audit history.
- `DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO` defaults to `0.5`; runs above this repeated-card ratio are skipped before Discord channel lookup, purge, thread creation, or message posting.
- `DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS` defaults to `2` so thin/title-only selections do not become public daily card-news.
- `DISCORD_CARD_NEWS_CONTENT_SIMILARITY_THRESHOLD` defaults to `0.72`; lower values catch more cross-URL repeats, while higher values reduce false positives between related but distinct articles.
- `DISCORD_CARD_NEWS_AGENT_DEDUPE` defaults to disabled (`0`). Set it to `1` to ask the loopback OpenClaw agent to judge same-article/same-story duplicates from bounded public context when URL/signature checks are not enough. It uses `OPENCLAW_BASE_URL`, `OPENCLAW_GATEWAY_TOKEN`/`OPENCLAW_GATEWAY_TOKEN_FILE`, and `OPENCLAW_MODEL`.
- `DISCORD_CARD_NEWS_AGENT_DEDUPE_MAX_PREVIOUS` defaults to `5` ranked previous contexts per run; `DISCORD_CARD_NEWS_AGENT_DEDUPE_TIMEOUT_SEC` defaults to `45`.
- `DISCORD_CARD_NEWS_AUDIT_PATH` optionally overrides the JSONL audit/history file. If unset, the default path is `$NEWSLETTER_WIKI_ROOT/state/card-news-publication-audit.jsonl` when `NEWSLETTER_WIKI_ROOT` is configured, otherwise `~/.openclaw/state/discord-openclaw-bridge/card-news-publication-audit.jsonl`.

Quality gate behavior:

- The gate affects only public Discord card-news publication. It does not modify raw newsletter archive creation, newsletter wiki files, or the source `items.json`.
- On a skip decision, the publisher writes a sanitized audit record, prints a one-line `skipped card news quality_gate ...` result with reason/count fields, and exits before Discord side effects. Existing card-news posts are not purged on skip.
- Skip reason codes are threshold-oriented: not enough publishable cards, not enough evidence-backed cards, not enough new cards, or too much overlap with recent published card-news. Repetition is counted by URL/story identity, hashed content-signature similarity, or optional OpenClaw agent judgment, so the same article can be blocked even when it arrives through a different public URL. Operators should inspect the audit record before relaxing thresholds.
- On a publish decision, the audit record includes sanitized thresholds/counts and optional Discord result metadata such as message count, purged count, and thread id.
- If a failure occurs after Discord side effects have begun, the publisher makes a best-effort `decision=failure` audit entry with the failed stage and sanitized counts/error class before surfacing the error. Treat this as the first place to check after a partial purge/thread/post failure.
- Audit records must contain only public/sanitized metadata: public titles, sanitized URLs or URL hashes, topic labels, fingerprints, hashed content-signature tokens, bounded public agent context, evidence kind/richness, thresholds, counts, reason codes, and publish metadata. They must not contain raw Gmail bodies, OAuth tokens, Discord tokens, relay/gateway tokens, webhook URLs, full env dumps, or absolute local/EC2 source paths by default.

Privacy boundary: the renderer and quality gate use sanitized archive fields (`article_title`, `summary_lines`, `why_now`, `claim`, `mechanism`, `evidence`, public excerpt/description, source name, URL, topic labels/reasons). They do not read or post Gmail bodies, OAuth tokens, Script Properties, webhook URLs, relay tokens, or OpenClaw gateway secrets. Discord embeds are suppressed for posted messages.


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
- If the quality gate skips a legitimate urgent follow-up, temporarily relax `DISCORD_CARD_NEWS_MIN_NEW_CARDS` / `DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO` or set `DISCORD_CARD_NEWS_QUALITY_GATE=0`, then keep the audit line for follow-up calibration.
- Review `DISCORD_PURGE_PREVIOUS_CARD_NEWS` before production posting. It defaults to enabled for replacement posts and removes prior bot-authored card-news posts/active card-news forum threads.
- If using `DISCORD_CARD_NEWS_HERO_IMAGE_PATH`, use a non-secret PNG asset intended for public Discord posting.

Post-publish checks:

- Confirm the header card shows date, theme, and selected/collected counts.
- Confirm item cards keep the card-news narrative arc and suppress link embeds.
- Confirm the audit JSONL contains exactly one sanitized decision record for the run (`publish`, `skip`, or `failure`) and no raw email text or secret-like values.
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
