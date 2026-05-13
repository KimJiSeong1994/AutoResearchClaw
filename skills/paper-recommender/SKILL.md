---
name: paper-recommender
description: Daily paper recommender that runs on the OpenClaw EC2 host. Uses 집현전 bookmarks as the profile source, gathers candidates via 집현전 search + citation graph, reranks through the OpenClaw gateway, and writes an Obsidian daily note.
---

# paper-recommender

Use this skill when the user asks to:

- set up, repair, or validate the paper recommender pipeline
- trigger a one-off recommendation run
- install / remove the daily cron
- sync the latest recommendations into Obsidian
- inspect the remote paper-recommender project

## Remote layout

- Project dir: `~/.openclaw/workspace/projects/paper-recommender`
- Config: `~/.openclaw/workspace/projects/paper-recommender/config.yaml`
- Entry: `.venv/bin/paper-recommender`
- Gateway endpoint: `http://127.0.0.1:18789/v1`
- Gateway model target: `openclaw/clawbridge`
- Output: `~/.openclaw/workspace/projects/paper-recommender/artifacts/YYYY-MM-DD/`

## Local flows

Run these helpers from the skill directory:

- `{baseDir}/bootstrap-remote.sh` — one-time setup (rsync project, venv, config, 집현전 token)
- `{baseDir}/deploy.sh` — push code-only updates to EC2 (no venv touch)
- `{baseDir}/run-once.sh` — trigger a run on EC2 and sync results back
- `{baseDir}/sync-results.sh` — rsync artifacts only
- `{baseDir}/status.sh` — show the latest remote run log + artifact tree
- `{baseDir}/doctor.sh` — check jiphyeonjeon + OpenClaw connectivity end-to-end
- `{baseDir}/install-cron.sh` — install the 08:00 KST daily crontab entry on EC2
- `{baseDir}/uninstall-cron.sh` — remove the daily crontab entry

## Operating rules

- Keep the OpenClaw gateway on loopback only.
- `JIPHYEONJEON_TOKEN` must be present on EC2 as a bearer JWT. The bootstrap stores it in `~/.openclaw/workspace/projects/paper-recommender/.env`; rotate it before the JWT expires.
- Before the first real run after config changes, run `doctor.sh`.
- A future "profile management page" will overwrite `profile.seed_topics` and short-circuit the LLM profile builder. Until then the profile is derived from 집현전 bookmarks.
- The daily cron runs the pipeline on EC2. Syncing to Obsidian is a separate step (`sync-results.sh`), intentionally decoupled from the cron.
- For non-trivial pipeline changes, state assumptions and success criteria before editing.
- Keep implementation minimal: do not add ranking strategies, profile signals, caches, or output formats beyond the requested goal.
- Make surgical changes and clean up only unused code introduced by the current change.
- Verify with the narrowest relevant check first (`doctor.sh`, targeted tests, or one-off run), then broaden only if needed.

## Obsidian target

Daily recommendations sync to:

`<LOCAL_AUTORESEARCHCLAW_SYNC_DIR>/recommendations/`

Weekly trend reports sync cumulatively to:

`<LOCAL_PAPER_REVIEW_DIR>/`

Each daily subdir holds `recommendations.md` (the Obsidian note) and `raw.json` (debug dump).
