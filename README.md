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
- Local helper scripts:
  - `scripts/deploy-openclaw-workspace.sh`
  - `scripts/openclaw-dashboard-tunnel.sh`
  - `scripts/run-researchclaw-topic.sh`
  - `scripts/sync-researchclaw-results.sh`

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
