# paper-recommender skill

Daily paper recommendation pipeline that runs on the OpenClaw EC2 host and writes an Obsidian daily note. Personalizes via a **per-user evolving SOUL.md** keyed on the 집현전 JWT subject. A/B mode keeps measuring `keywords` baseline against the SOUL-driven rerank so the value of evolution is observable, not assumed.

## What it does

1. **Profile** — pulls 집현전 bookmarks and asks the OpenClaw LLM to produce two cached profile artifacts (7-day TTL):
   - `state/profile.json` — structured (interests / keywords / methods)
   - `state/profile.md` — narrative seed (used to bootstrap a fresh SOUL)
2. **SOUL evolve** — for the user derived from the JWT `sub` claim:
   - Loads `state/souls/{user_id}.md` (or bootstraps from the narrative seed)
   - Diffs new bookmarks since the last `bump_soul_update` and pulls recent picks from `state/ab_log.jsonl`
   - LLM **evolve** call: preserves prior content, folds new signals into the four core sections (`Research trajectory`, `Methodology stance`, `Recurring obsessions`, `Blind spots`), updates `Suppress keywords`, appends one dated line to `Changelog`
   - If new SOUL exceeds `compact_at_bytes` (2560 default), runs an LLM **compact** call targeting `max_bytes` (3072) — keeps the 8 most recent Changelog entries verbatim, summarizes older ones into one leading line
   - All inputs fenced inside `<prior_soul>` / `<new_bookmarks>` / `<recent_picks>` data blocks; the system prompt explicitly forbids treating them as instructions
3. **Candidates** — runs 집현전 `/api/search` against profile keywords + `/api/bookmarks/{id}/citation-tree` on the newest bookmarks. Dedupes, removes already-bookmarked + 30-day-cooldown papers. Then **filters out anything matching SOUL's `Suppress keywords`** (case-insensitive substring on title/abstract/seed).
4. **Rerank (A/B)** — depending on `rerank.mode`:
   - `keywords` / `narrative` / `soul` — single rerank with that profile representation
   - `ab` (default) — runs **two** variants on the same candidate pool and stores both pick lists side-by-side. With SOUL enabled this becomes `keywords` vs `soul`; with SOUL disabled it falls back to `keywords` vs `narrative`
   - System prompt fences profile + candidates inside `<reader_profile>` / `<candidates>` data blocks
5. **Write** — emits `artifacts/YYYY-MM-DD/`:
   - `recommendations.md` — Obsidian note: A/B header (Jaccard), profile snapshot, SOUL collapsed in `<details>`, both pick lists
   - `souls/{user_id}.md` — SOUL snapshot for this run (read-only mirror of state)
   - `profile.md` — narrative profile (when produced this run)
   - `raw.json` — full run dump
   - `state/ab_log.jsonl` (append-only) — per-run pick IDs + Jaccard + SOUL byte size

## Per-user SOUL: where it lives & how it grows

```
state/
├── souls/
│   ├── {user_id}.md         # the evolving soul (atomic writes)
│   └── …                    # one file per user when multi-user lands
├── soul_meta.json           # {user_id: {last_update, last_bookmark_id}}
├── profile.json             # structured cache (7-day TTL)
├── profile.md               # narrative cache (7-day TTL)
├── seen.json                # 30-day cooldown registry
├── ab_log.jsonl             # per-run A/B record
└── runs.jsonl               # per-run pipeline summary
```

`user_id` is derived from the JWT `sub` claim, then sanitized via an allowlist (`[a-zA-Z0-9_-]`, max 64 chars, no leading dots/dashes). Path traversal payloads in `sub` collapse to underscores and cannot escape the `state/souls/` directory. If the JWT has no `sub`, the pipeline falls back to `anon_<sha256_prefix>` so a token rotation still keeps the SOUL isolated.

## A/B decision criteria (after ~2 weeks of `ab_log.jsonl`)

The deep-design review demanded measurable evidence that a richer profile actually changes top-K picks before further investment. With SOUL active, the comparison runs every day automatically.

| Observed signal | Action |
|---|---|
| Jaccard ≥ 0.8 sustained | SOUL and keywords pick nearly the same papers — evolution is noise. Flip `rerank.mode: keywords`, optionally disable `soul.enabled`. |
| Jaccard 0.4–0.8 with `soul` picks consistently more useful when reading the daily note | SOUL is real signal. Flip `rerank.mode: soul`, keep keyword profile only as candidate-search input. |
| Jaccard 0.4–0.8, no clear winner | Keep `ab` mode another 2 weeks. |
| Jaccard < 0.4 | Variants disagree wildly — instability somewhere in the rerank prompt or SOUL drift. Investigate before keeping. |

Quick trend command:

```bash
ssh ubuntu@52.79.96.56 'jq -c "{date: .run_at, jaccard, soul_b: .soul_bytes, k: (.variants.keywords|length), s: (.variants.soul|length)}" \
  ~/.openclaw/workspace/projects/paper-recommender/state/ab_log.jsonl'
```

## First-time setup

```bash
cd /Users/jiseong/git/AutoResearchClaw

bash skills/paper-recommender/bootstrap-remote.sh   # deploy + venv + .env (JWT via stdin only)
bash skills/paper-recommender/doctor.sh             # smoke-test 집현전 + OpenClaw
bash skills/paper-recommender/run-once.sh           # first real run + sync to Obsidian
bash skills/paper-recommender/install-cron.sh       # daily 08:00 KST (= UTC 23:00)
```

## Day-to-day

```bash
bash skills/paper-recommender/run-once.sh           # manual trigger + sync
bash skills/paper-recommender/sync-results.sh       # pull whatever cron produced (--safe-links, 10MB cap)
bash skills/paper-recommender/status.sh             # last run + cron entry
bash skills/paper-recommender/deploy.sh             # rsync code-only updates (no venv touch)
```

## Newsletter intake (local export only)

Google-account newsletter ingestion is intentionally a local-export boundary:
the repo does not authenticate to Gmail, store OAuth credentials, or read a
private mailbox. Export or sanitize the newsletters yourself (for example Gmail
Takeout `.mbox` or JSONL with `subject`, `from`, `date`, and `body` fields),
then publish extracted research/post links into the LLM Wiki:

```bash
python3 skills/paper-recommender/newsletter_ingest.py \
  --source ~/Downloads/google-newsletters.mbox \
  --wiki-root "/Users/jiseong/Library/Mobile Documents/com~apple~CloudDocs/PaperWiki/PaperWiki" \
  --sender-allowlist "newsletter,research,arxiv"
```

Outputs are raw-first and idempotent for the selected date:

- `raw/newsletters/YYYY-MM-DD/items.json`
- `pages/newsletter-ingest-YYYY-MM-DD.md`

Only message metadata and extracted URLs are written; full email bodies and
credentials are omitted from wiki outputs. The CLI requires either
`--sender-allowlist` or the explicit `--allow-all-senders` override, and caps
source size/message count by default so broad Gmail exports are not processed
accidentally. See
`skills/paper-recommender/newsletter-config.example.json` for a non-secret
configuration template.

## Configuration knobs

`~/.openclaw/workspace/projects/paper-recommender/config.yaml`:

- `profile.seed_topics` — fallback keywords (cold start; future profile management page will overwrite)
- `profile.narrative_enabled` — extra LLM call to seed SOUL with a fresh narrative when missing
- `candidates.per_keyword`, `candidates.total_cap` — retrieval breadth
- `rerank.mode` — `keywords` / `narrative` / `soul` / `ab` (see decision table above)
- `rerank.batch_size`, `rerank.top_k`, `rerank.min_score` — rerank budget and output size
- `seen.cooldown_days` — recommendation deduplication window
- `soul.enabled` / `soul.update_cadence_days` / `soul.max_bytes` / `soul.compact_at_bytes` / `soul.include_recent_picks_days` — SOUL evolution controls

## Tokens & secrets

- **OpenClaw gateway token** — read from `~/.openclaw_gateway_token` at run time (loopback only)
- **집현전 JWT** — stored in `~/.openclaw/workspace/projects/paper-recommender/.env` as `JIPHYEONJEON_TOKEN`. Bootstrap delivers it over SSH **stdin** (never on the command line, so it does not appear in `ps`/shell history) and writes via temp + atomic rename
- The pipeline decodes the JWT payload only to read `sub` for filename routing — no signature verification, no authorization decisions made from the token contents

## Obsidian output

Daily recommendations sync to:

`/Users/jiseong/Library/Mobile Documents/iCloud~md~obsidian/Documents/Write Paper/AutoResearchClaw/recommendations/YYYY-MM-DD/`

Weekly trend reports sync cumulatively to:

`/Users/jiseong/Library/Mobile Documents/iCloud~md~obsidian/Documents/PaperReview/`

Each daily directory contains the daily note + `souls/{user_id}.md` snapshot + raw artifacts. Tags `paper-recommender` / `daily` are set in frontmatter for indexing or dataview queries.

Weekly trend reports are copied separately and cumulatively to the PaperReview vault, preserving the remote `YYYY-Www/` weekly subdirectories without deleting older local reports.

The Obsidian copy of `souls/{user_id}.md` is **read-only by convention** today — sync is one-way (EC2 → vault). If you want hand-edits in Obsidian to flow back as authoritative signals, that's a separate bidirectional-sync feature (with conflict resolution and prompt-injection sanitization) tracked as future work.

## Personalization signals beyond the SOUL baseline

After the SOUL layer, three additional signals plug into the same evolve loop:

### Time decay (always on)
Each bookmark gets a `_weight = max(0.05, 0.5^(age_days / half_life_days))` (default half-life 60 days). Weights sort the bookmark list newest-effective first before the `max_bookmarks_for_profile` truncation, and surface to the LLM as `[w=0.83]` prefixes in both profile and SOUL evolve prompts. Set `decay.half_life_days: 0` to disable.

### Read / dislike feedback markers (Obsidian → EC2)
Inside any `recommendations.md` daily note in your Obsidian vault, you can annotate paper sections:

```markdown
#### 1. Some Paper Title
- Score: 5
- Links: [arXiv](...)
[read]                                ← positive signal

#### 2. Other Paper
[dislike: too systems-y, not my area] ← becomes a Suppress keyword
```

`sync-results.sh` ships the last `feedback.lookback_days` (default 7) of these notes back to EC2 `state/feedback_inbox/`, with **multi-layer guards** against feedback-loop drift and prompt injection:

| Layer | Defense | Where |
|---|---|---|
| Sync | symlink rejection + realpath containment + iCloud `.icloud` placeholder skip + 512 KB cap | `sync-results.sh` |
| Parse | frontmatter `date:` must be within `lookback_days` and not future-dated; regex allowlist `[read]` / `[dislike: <reason>]`; reason ≤ 200 chars | `signals.parse_feedback_markers` |
| Persist | append-then-trust — if the JSONL append fails, the in-memory records are dropped so SOUL never sees an unlogged signal | `pipeline._collect_feedback` |
| LLM | `<` and `>` in user-supplied text escaped to `&lt;`/`&gt;` so the prompt's `<user_feedback>` fence cannot be broken; SOUL system prompt explicitly marks all inputs as DATA | `soul._safe_text` |
| Suppress | reasons split on commas, fragments < 3 chars dropped, dedup case-insensitive, max 50 active terms | `pipeline._expand_suppress_terms` |

Feedback flows two places per run: (1) the dislike `reason` becomes an immediate suppress filter on this run's candidates, and (2) the full record is fed into SOUL evolve as `<user_feedback>` so the next SOUL captures the pattern in its narrative + Suppress keywords sections.

### Deep-review integration — DEFERRED
집현전 has no list-past-reviews HTTP endpoint (`/api/deep-review/{report,status}/{id}` only — no list). A bookmark-metadata-scanning workaround was rejected as too fragile. Will revisit when 집현전 exposes a list endpoint.

## Mandatory next step before any further feature work

This codebase has been written and unit-tested but has never produced a real run. Before adding more signals or tuning, the deploy-and-observe order is:

1. `bash skills/paper-recommender/bootstrap-remote.sh`
2. `bash skills/paper-recommender/doctor.sh`
3. `bash skills/paper-recommender/run-once.sh` — first real artifact
4. **Wait at least 5 days**, accumulating `ab_log.jsonl` rows
5. Read `state/ab_log.jsonl` and the daily notes; only then decide whether SOUL beats keywords (Jaccard table in this README) and whether feedback markers are useful

Adding personalization features without this observation window risks compounding unvalidated complexity — the same trap the design review explicitly flagged.

## Deferred / not built

The deep-design review (architect / critic / security-reviewer / code-reviewer) explicitly flagged these as either premature or risky; they remain unimplemented:

- Bidirectional Obsidian sync of SOUL files — race + injection surface; unidirectional today
- Monthly scratch-rebuild reconciliation against the evolved SOUL — `## Changelog` is the audit trail for now; reconcile manually if drift suspected
- Multi-tenant 집현전 scope — single user per token; admin endpoints to enumerate other users' bookmarks not yet wired
- Read-only-scoped JWT — current token is admin-scoped (security finding #5); rotate to a read-only token before exposing this beyond a personal pipeline
