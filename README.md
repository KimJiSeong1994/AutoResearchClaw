# AutoResearchClaw

Local, version-controlled workspace for the OpenClaw agent running on the EC2 gateway.

## What this repo contains

- OpenClaw workspace source files:
  - `workspace/AGENTS.md`
  - `workspace/IDENTITY.md`
  - `workspace/SOUL.md`
  - `workspace/TOOLS.md`
  - `workspace/USER.md`
  - `workspace/MEMORY.md`
  - `workspace/HEARTBEAT.md`
- Custom workspace skill:
  - `skills/openclaw-ec2-ops/`
  - `skills/karpathy-guidelines/`
  - `skills/researchclaw/`
  - `skills/discord-openclaw-bridge/`
- Local helper scripts:
  - `scripts/deploy-openclaw-workspace.sh`
  - `scripts/openclaw-dashboard-tunnel.sh`
  - `scripts/run-researchclaw-topic.sh`
  - `scripts/sync-researchclaw-results.sh`
  - `scripts/deploy-discord-openclaw-bridge.sh`

## Target runtime

- EC2 host: `52.79.96.56`
- SSH user: `ubuntu`
- Remote OpenClaw workspace: `~/.openclaw/workspace`
- Gateway bind: `127.0.0.1:18789`

## Agent discipline

The workspace and skills apply Karpathy-inspired agent behavior from
`forrestchang/andrej-karpathy-skills`: surface assumptions, prefer simple
solutions, edit surgically, and define verifiable success criteria before
claiming completion.

## Deploy workspace changes

```bash
bash scripts/deploy-openclaw-workspace.sh
```

The deploy script maps `workspace/*.md` into the remote OpenClaw workspace root.

## Deploy Discord OpenClaw bridge

```bash
bash scripts/deploy-discord-openclaw-bridge.sh
```

On EC2, set `DISCORD_BOT_TOKEN` in `~/.openclaw/workspace/skills/discord-openclaw-bridge/project/.env`, run `bash project/scripts/install.sh`, then start `discord-openclaw-bridge.service`. The bridge is allowlisted to Discord guild `1500743272551813142` and channel `1500743273361440823` by default.

## Check remote ops readiness

```bash
bash scripts/check-openclaw-ops.sh
```

This read-only check verifies the remote gateway service, loopback listeners, `/v1/models` probe, Discord bridge service, ResearchClaw install surface, latest paper-recommender status, and recent OpenClaw warning/error log signal without printing gateway tokens.

## Open the dashboard through SSH

```bash
bash scripts/openclaw-dashboard-tunnel.sh
```

Then open:

```text
http://127.0.0.1:18789
```

## Run AutoResearchClaw and sync results into Obsidian

```bash
bash scripts/run-researchclaw-topic.sh "Your research topic"
```

Synced local output root:

```text
/Users/jiseong/Library/Mobile Documents/iCloud~md~obsidian/Documents/Write Paper/AutoResearchClaw
```
