# TOOLS.md - Environment Notes

## Remote host

- Public IP: `<EC2_PUBLIC_IP>`
- Public DNS: `<EC2_PUBLIC_DNS>`
- SSH user: `ubuntu`
- Key file on local machine: `<PATH_TO_SSH_PRIVATE_KEY>`

## Remote Hermes

- Workspace: `~/.hermes/workspace`
- Config: `~/.hermes/config.yaml`
- Secrets: `~/.hermes/.env`
- Gateway token file: `~/.hermes_gateway_token`
- Gateway listener: `127.0.0.1:28789`
- systemd user service: `hermes-gateway.service`
- Runtime log dir: `~/.hermes/logs`

## Local helper flows

- Deploy this repo into the remote workspace:
  - `bash scripts/deploy-hermes-workspace.sh`

## Discord Hermes bridge

- Bridge project: `~/.hermes/workspace/skills/discord-openclaw-bridge/project`
- Service: `discord-hermes-bridge.service`
- Default Discord guild: `<DISCORD_GUILD_ID>`
- Default Discord channel: `<DISCORD_ALLOWED_CHANNEL_ID>`
- Runtime calls Hermes through `http://127.0.0.1:28789/v1`; do not expose the gateway publicly for Discord.
- Secret file: bridge project `.env` contains `DISCORD_BOT_TOKEN` and must not be version-controlled.

## Safety defaults

- Loopback-only gateway is the default.
- Use SSH tunneling for dashboard access.
- Never paste the raw token into version-controlled files.

## AutoResearchClaw integration

- Project root on EC2: `~/.hermes/workspace/projects/AutoResearchClaw`
- Python runtime: `~/.local/bin/uv` + managed Python `3.11`
- Virtualenv: `~/.hermes/workspace/projects/AutoResearchClaw/.venv`
- Main config: `~/.hermes/workspace/projects/AutoResearchClaw/config.yaml`
- Context file for Hermes bootstrap: `~/.hermes/workspace/projects/AutoResearchClaw/RESEARCHCLAW_AGENTS.md`
- Gateway endpoint for ResearchClaw LLM calls: `http://127.0.0.1:28789/v1`
- Gateway model target: `hermes-agent`
- Env var for auth when running ResearchClaw: `HERMES_GATEWAY_TOKEN`
- Synced local output root: `<LOCAL_OBSIDIAN_SYNC_DIR>`
- Local sync helper: `bash scripts/sync-researchclaw-results.sh`
- Local run+sync helper: `bash scripts/run-researchclaw-topic.sh "topic"`
