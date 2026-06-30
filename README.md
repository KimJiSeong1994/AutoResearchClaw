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

## Run SkillOpt readiness audit

```bash
python3 scripts/skillopt_audit.py \
  --codex-skills .codex/skills \
  --runtime-skills skills \
  --agents runtime/agents.yaml \
  --jobs runtime/jobs.yaml \
  --out .omx/reports/skillopt/skillopt-audit-latest.json \
  --markdown
```

The audit is a local read-only control-plane check for SkillOpt-style skill
improvement. It inventories `.codex/skills/*/SKILL.md`, `skills/*/SKILL.md`,
and `skills/*/README.md`, maps them to runtime agents/jobs, and emits stable
`gap_code` findings plus a Markdown gap matrix. PaperWiki evidence imports must
use wiki-relative paths only; generated reports must not contain absolute local
vault paths, note bodies, tokens, or webhook URLs.

## Run SkillOpt evaluation harness

```bash
python3 scripts/skillopt_eval.py \
  --fixtures tests/fixtures/skillopt \
  --out .omx/reports/skillopt/skillopt-eval-latest.json
```

The evaluation harness is the Phase 2 gate before SkillOpt bounded edits can be
accepted. It runs deterministic held-out fixtures for `academic-technical-filter`,
`blog-research-post`, and `jiphyeonjeon-reporter-article-post`, preserves a JSON
acceptance record, and keeps automatic skill mutation disabled until reviewer and
critic gates approve a proposed patch.

## Generate SkillOpt patch proposals

```bash
python3 scripts/skillopt_propose.py \
  --audit .omx/reports/skillopt/skillopt-audit-latest.json \
  --eval .omx/reports/skillopt/skillopt-eval-latest.json \
  --out-dir .omx/reports/skillopt/patch-candidates \
  --as-of 2026-06-27T00:00:00+09:00
```

Reject a candidate without editing any skill file:

```bash
python3 scripts/skillopt_propose.py reject \
  .omx/reports/skillopt/patch-candidates/<skill>/<proposal>.json \
  --reason "weak evidence" \
  --buffer .omx/reports/skillopt/rejected-edits.jsonl
```

Phase 3 proposal generation is deterministic and read-only for skill/runtime
surfaces. Use `--as-of` for reproducible timestamps; when omitted, proposal
timestamps are inherited from the audit/eval input report where available. The
script creates reviewer-gated JSON candidates, suppresses repeated rejected
fingerprints, and validates accepted-lineage schemas for Phase 4. Actual skill
mutation and live `accepted-lineage.jsonl` writes remain a separate controlled
apply step after reviewer and critic gates.

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


## Score SkillOpt rewards

```bash
python3 scripts/skillopt_reward.py score \
  --audit .omx/reports/skillopt/skillopt-audit-latest.json \
  --eval .omx/reports/skillopt/skillopt-eval-latest.json \
  --candidate-dir .omx/reports/skillopt/patch-candidates \
  --accepted-lineage .omx/reports/skillopt/accepted-lineage.jsonl \
  --rejected-buffer .omx/reports/skillopt/rejected-edits.jsonl \
  --out .omx/reports/skillopt/skillopt-reward-latest.json
```

The reward report is Phase 5 advisory evidence. It emits `skillopt-reward.v1`
`eval_reward` and `proposal_reward` records with deterministic basis-point
scores, confidence, coverage, explanations, penalties, and privacy guards.
Reward may help rank candidates only after hard exclusions; it never approves,
applies, or mutates skill files. Low-confidence or low-coverage proposal rewards
fallback to the legacy deterministic rank tuple.

Use reward-aware selection only as an ordering aid for eligible candidates:

```bash
python3 scripts/skillopt_apply.py select \
  --candidate-dir .omx/reports/skillopt/patch-candidates \
  --reward-report .omx/reports/skillopt/skillopt-reward-latest.json \
  --out .omx/reports/skillopt/apply-runs/<timestamp>-selection.json
```

## Apply one SkillOpt proposal under controlled gates

Phase 4 controlled apply is the first SkillOpt step allowed to mutate a skill
file, and only for one selected proposal at a time. Start with deterministic
selection and a no-mutation dry-run:

```bash
python3 scripts/skillopt_apply.py select \
  --candidate-dir .omx/reports/skillopt/patch-candidates \
  --out .omx/reports/skillopt/apply-runs/<timestamp>-selection.json

python3 scripts/skillopt_apply.py dry-run \
  .omx/reports/skillopt/patch-candidates/<skill>/<proposal>.json \
  --selection-report .omx/reports/skillopt/apply-runs/<timestamp>-selection.json \
  --out .omx/reports/skillopt/apply-runs/<timestamp>-dry-run.json
```

Apply only after the dry-run diff is reviewed and both reviewer and critic
verdicts are `APPROVE`:

```bash
python3 scripts/skillopt_apply.py apply \
  .omx/reports/skillopt/patch-candidates/<skill>/<proposal>.json \
  --selection-report .omx/reports/skillopt/apply-runs/<timestamp>-selection.json \
  --dry-run-report .omx/reports/skillopt/apply-runs/<timestamp>-dry-run.json \
  --reviewer-verdict APPROVE \
  --critic-verdict APPROVE \
  --eval-before .omx/reports/skillopt/skillopt-eval-before.json \
  --eval-after .omx/reports/skillopt/skillopt-eval-after.json \
  --lineage .omx/reports/skillopt/accepted-lineage.jsonl \
  --out .omx/reports/skillopt/apply-runs/<timestamp>-apply.json
```

The apply gate rejects stale baselines, selection-report or dry-run-report
mismatches, missing approvals, privacy-risk text, ambiguous sections, failed
eval-after reports, and protected runtime paths. If post-apply validation fails, the script restores the
original skill content and leaves accepted lineage unchanged. `accepted-lineage.jsonl`
is append-only and written only as the final side effect after apply evidence is
complete. Do not batch-apply candidates.
