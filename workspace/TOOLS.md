# TOOLS.md - Environment Notes

## Remote host

- Public IP: `52.79.96.56`
- Public DNS: `ec2-52-79-96-56.ap-northeast-2.compute.amazonaws.com`
- SSH user: `ubuntu`
- Key file on local machine: `/Users/jiseong/git/PaperReviewAgent/jiseong.pem`

## Remote OpenClaw

- Workspace: `~/.openclaw/workspace`
- Config: `~/.openclaw/openclaw.json`
- Gateway token file: `~/.openclaw_gateway_token`
- Gateway listener: `127.0.0.1:18789`
- Browser control sidecar: `127.0.0.1:18791`
- systemd user service: `openclaw-gateway.service`
- Runtime log dir: `/tmp/openclaw`

## Local helper flows

- Deploy this repo into the remote workspace:
  - `bash scripts/deploy-openclaw-workspace.sh`
- Open local SSH tunnel to the dashboard:
  - `bash scripts/openclaw-dashboard-tunnel.sh`

## Discord OpenClaw bridge

- Bridge project: `~/.openclaw/workspace/skills/discord-openclaw-bridge/project`
- Service: `discord-openclaw-bridge.service`
- Default Discord guild: `1500743272551813142`
- Default Discord channel: `1500743273361440823`
- Runtime calls OpenClaw through `http://127.0.0.1:18789/v1`; do not expose the gateway publicly for Discord.
- Secret file: bridge project `.env` contains `DISCORD_BOT_TOKEN` and must not be version-controlled.

## Safety defaults

- Loopback-only gateway is the default.
- Use SSH tunneling for dashboard access.
- Never paste the raw token into version-controlled files.

## AutoResearchClaw integration

- Project root on EC2: `~/.openclaw/workspace/projects/AutoResearchClaw`
- Python runtime: `~/.local/bin/uv` + managed Python `3.11`
- Virtualenv: `~/.openclaw/workspace/projects/AutoResearchClaw/.venv`
- Main config: `~/.openclaw/workspace/projects/AutoResearchClaw/config.yaml`
- Context file for OpenClaw bootstrap: `~/.openclaw/workspace/projects/AutoResearchClaw/RESEARCHCLAW_AGENTS.md`
- Gateway endpoint for ResearchClaw LLM calls: `http://127.0.0.1:18789/v1`
- Gateway model target: `openclaw/clawbridge`
- Env var for auth when running ResearchClaw: `OPENCLAW_GATEWAY_TOKEN`
- Synced local output root: `/Users/jiseong/Library/Mobile Documents/iCloud~md~obsidian/Documents/Write Paper/AutoResearchClaw`
- Local sync helper: `bash scripts/sync-researchclaw-results.sh`
- Local run+sync helper: `bash scripts/run-researchclaw-topic.sh "topic"`
