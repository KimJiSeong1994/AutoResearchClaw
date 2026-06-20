# AutoResearchClaw

AI/ML 연구자를 위한 개인 맞춤형 리서치 비서. 논문 북마크·관심사를 학습해 매일 새 논문과 뉴스레터를 자동으로 골라 요약하고, 심층 리서치까지 Obsidian·Discord로 전달한다.

이 저장소는 위 서비스를 EC2 OpenClaw 워크스페이스로 배포·운영하는 소스(에이전트 프롬프트, 커스텀 스킬, 런타임 매니페스트, 배포 스크립트)를 담는다.

## What this repo contains

- OpenClaw workspace source files:
  - `workspace/AGENTS.md`
  - `workspace/IDENTITY.md`
  - `workspace/SOUL.md`
  - `workspace/TOOLS.md`
  - `workspace/USER.md`
  - `workspace/MEMORY.md`
  - `workspace/HEARTBEAT.md`
  - `workspace/PROMPT_GOVERNANCE.md`
  - `workspace/PROMPT_REGISTRY.json`
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

- EC2 host: `<EC2_PUBLIC_IP>`
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

The deploy script validates prompt governance first, then maps the workspace
control files and prompt registry into the remote OpenClaw workspace root.

## Validate governance and runtime manifests

```bash
python3 scripts/check-prompt-governance.py
python3 scripts/check-runtime-manifests.py
python3 -m unittest tests/test_prompt_governance.py
python3 -m unittest tests/test_runtime_manifests.py
```

The validators enforce the Jiphyeonjeon-Claw prompt inventory, lifecycle,
reporting status schema, runtime job/agent manifest cross-references,
source-file references, and secret-value guardrails.

## Deploy Discord OpenClaw bridge

```bash
bash scripts/deploy-discord-openclaw-bridge.sh
```

On EC2, set `DISCORD_BOT_TOKEN` in `~/.openclaw/workspace/skills/discord-openclaw-bridge/project/.env`, run `bash project/scripts/install.sh`, then start `discord-openclaw-bridge.service`. The bridge is allowlisted to Discord guild `<DISCORD_GUILD_ID>` and channel `<DISCORD_ALLOWED_CHANNEL_ID>` by default.

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
<LOCAL_AUTORESEARCHCLAW_SYNC_DIR>
```
