---
name: researchclaw
description: Set up and run the AutoResearchClaw pipeline through the local Hermes gateway on the EC2 host.
---

# ResearchClaw via Hermes

Use this skill when the user asks to:

- research a topic with AutoResearchClaw
- set up or repair the AutoResearchClaw integration
- validate the ResearchClaw environment
- inspect the cloned AutoResearchClaw repo/config

## Project location

- Repo: `~/.hermes/workspace/projects/AutoResearchClaw`
- Config: `~/.hermes/workspace/projects/AutoResearchClaw/config.yaml`
- Gateway endpoint: `http://127.0.0.1:28789/v1`
- Gateway model target: `hermes-agent`

## Preferred commands

Run these helpers from the skill directory:

- `{baseDir}/bootstrap-remote.sh`
- `{baseDir}/status.sh`
- `{baseDir}/validate.sh`
- `{baseDir}/doctor.sh`
- `{baseDir}/run-topic.sh "your topic"`

## Operating rules

- Export `HERMES_GATEWAY_TOKEN` from `~/.hermes_gateway_token` before running ResearchClaw commands.
- Keep ResearchClaw pointed at the loopback Hermes gateway, not the public network.
- Prefer `researchclaw validate` before the first real run after config changes.
- Do not start a full paper run unless the user clearly asked for research execution.
- Before changing setup or pipeline code, make assumptions explicit and define the verification target.
- Keep changes surgical: avoid dependency swaps, config rewrites, or broad refactors unless the user asked for them.
- Prefer the simplest command or patch that proves the current ResearchClaw goal.

## Default setup contract

The bootstrap script should leave the host in this state:

1. Hermes `/v1/*` HTTP endpoint enabled
2. AutoResearchClaw cloned under `projects/AutoResearchClaw`
3. Python `3.11` available via `uv`
4. `.venv` created and `pip install -e .` completed
5. `config.yaml` wired to `http://127.0.0.1:28789/v1`
6. `RESEARCHCLAW_AGENTS.md` present for Hermes-friendly bootstrap context
