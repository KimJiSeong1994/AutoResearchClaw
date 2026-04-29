---
name: researchclaw
description: Set up and run the AutoResearchClaw pipeline through the local OpenClaw gateway on the EC2 host.
---

# ResearchClaw via OpenClaw

Use this skill when the user asks to:

- research a topic with AutoResearchClaw
- set up or repair the AutoResearchClaw integration
- validate the ResearchClaw environment
- inspect the cloned AutoResearchClaw repo/config

## Project location

- Repo: `~/.openclaw/workspace/projects/AutoResearchClaw`
- Config: `~/.openclaw/workspace/projects/AutoResearchClaw/config.yaml`
- Gateway endpoint: `http://127.0.0.1:18789/v1`
- Gateway model target: `openclaw/clawbridge`

## Preferred commands

Run these helpers from the skill directory:

- `{baseDir}/bootstrap-remote.sh`
- `{baseDir}/status.sh`
- `{baseDir}/validate.sh`
- `{baseDir}/doctor.sh`
- `{baseDir}/run-topic.sh "your topic"`

## Operating rules

- Export `OPENCLAW_GATEWAY_TOKEN` from `~/.openclaw_gateway_token` before running ResearchClaw commands.
- Keep ResearchClaw pointed at the loopback OpenClaw gateway, not the public network.
- Prefer `researchclaw validate` before the first real run after config changes.
- Do not start a full paper run unless the user clearly asked for research execution.
- Before changing setup or pipeline code, make assumptions explicit and define the verification target.
- Keep changes surgical: avoid dependency swaps, config rewrites, or broad refactors unless the user asked for them.
- Prefer the simplest command or patch that proves the current ResearchClaw goal.

## Default setup contract

The bootstrap script should leave the host in this state:

1. OpenClaw `/v1/*` HTTP endpoint enabled
2. AutoResearchClaw cloned under `projects/AutoResearchClaw`
3. Python `3.11` available via `uv`
4. `.venv` created and `pip install -e .` completed
5. `config.yaml` wired to `http://127.0.0.1:18789/v1`
6. `RESEARCHCLAW_AGENTS.md` present for OpenClaw-friendly bootstrap context
