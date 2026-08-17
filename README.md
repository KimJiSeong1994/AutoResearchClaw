# AutoResearchClaw

AI/ML 연구자를 위한 개인 맞춤형 리서치 비서. 논문 북마크·관심사를 학습해 매일 새 논문과 뉴스레터를 자동으로 골라 요약하고, 심층 리서치까지 Obsidian·Discord로 전달한다.

이 저장소는 위 서비스를 EC2 Hermes Agent 워크스페이스로 배포·운영하는 소스(에이전트 프롬프트, 커스텀 스킬, 런타임 매니페스트, 배포 스크립트)를 담는다. `openclaw`가 포함된 일부 패키지·명령 이름은 한 배포 주기 동안의 호환성 표면이며 실제 production 서비스와 경로는 Hermes를 사용한다.

## What this repo contains

- Hermes workspace source files:
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
  - `scripts/deploy-hermes-workspace.sh`
  - `scripts/run-researchclaw-topic.sh`
  - `scripts/sync-researchclaw-results.sh`
  - `scripts/deploy-discord-hermes-bridge.sh`
  - `scripts/install-hermes-primary-bridge-service.sh`

## Target runtime

- EC2 host: `<EC2_PUBLIC_IP>`
- SSH user: `ubuntu`
- Remote Hermes workspace: `~/.hermes/workspace`
- Gateway bind: `127.0.0.1:28789`
- Primary services: `hermes-gateway.service`, `discord-hermes-bridge.service`
- Rollback-only state: `~/.openclaw`, `openclaw-gateway.service`, `discord-openclaw-bridge.service` (disabled, not deleted)

## Agent discipline

The workspace and skills apply Karpathy-inspired agent behavior from
`forrestchang/andrej-karpathy-skills`: surface assumptions, prefer simple
solutions, edit surgically, and define verifiable success criteria before
claiming completion.

## Deploy workspace changes

```bash
bash scripts/deploy-hermes-workspace.sh
```

The deploy script validates prompt governance first, then maps the workspace
control files and prompt registry into the remote Hermes workspace root.

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
accepted. It runs 17 deterministic held-out fixtures for
`academic-technical-filter`, `blog-research-post`,
`jiphyeonjeon-reporter-article-post`, and `jiphyeonjeon-traveler`, preserves a
JSON acceptance record, and keeps automatic skill mutation disabled until
reviewer and critic gates approve a proposed patch.

## Evaluate Traveler operational evidence with SkillOpt

```bash
python3 scripts/skillopt_traveler_ops.py \
  --scout ~/.hermes/workspace/state/traveler-scout-last-status.json \
  --discovery ~/.hermes/workspace/state/traveler-source-discovery-last-status.json \
  --report ~/.hermes/workspace/state/traveler-collection-report-last-status.json \
  --reward ~/.hermes/workspace/state/traveler-skillopt-reward-latest.json \
  --workspace ~/.hermes/workspace \
  --out ~/.hermes/workspace/state/traveler-skillopt-latest.json
```

The Traveler adapter is read-only and emits `skillopt-traveler-ops.v1` with
exit code `0` (`ok`), `1` (`degraded`), or `2` (`failed`). It rejects stale or
future-dated evidence, legacy OpenClaw production paths, and false-green
zero-yield runs with provider errors. The daily Traveler job also records
exact-URL Miner handoff/approval events and scores a reward composed of 70%
Miner approval rate plus 30% approved provider/domain/topic Shannon diversity.
Recent unapproved handoffs are censored for seven days.
The latest append-only Miner/Claw decision is authoritative; the approved export
is only a fallback, and a later reject/hold revokes the positive label.

Traveler also reads 100 papers from alphaXiv's public Hot feed API in one
request, caches that pool, and ranks it locally against the public
query/scope fields exported from PaperWiki KG interests. The robots-allowed root
`Trending Papers` JSON-LD remains a 20-paper fallback. Private KG note bodies
are never transmitted, `/?sort=Hot` is never crawled, and every recommended
paper still passes evidence collection plus Claw/Miner review.

The evaluator remains read-only and cannot change the report exit code. A
separate bounded auto-tune sink activates only with at least five eligible
samples and may change only `priority`/`max_candidates` in
`runtime/traveler-scout-topics.json` plus the generated search-scope markers in
`skills/jiphyeonjeon-traveler/SKILL.md`. It requires exact target allowlists,
baseline hashes, versioned backups, post-validation, rollback, and append-only
lineage. Unrestricted SkillOpt apply and automatic scoring/provider/queue,
Discord, cron, systemd, or credential changes remain forbidden.

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

## Deploy the Discord bridge through Hermes

```bash
bash scripts/deploy-discord-hermes-bridge.sh
bash scripts/check-hermes-ops.sh
bash scripts/check-hermes-bridge-smoke.sh
bash scripts/install-hermes-primary-bridge-service.sh
```

On EC2, the bridge environment lives at `~/.hermes/workspace/skills/discord-openclaw-bridge/project/.env` and the active unit is `discord-hermes-bridge.service`. The installer promotes Hermes only after gateway and Discord readiness checks, disables the old OpenClaw units, and retains their state for rollback.

## Check remote ops readiness

```bash
bash scripts/check-hermes-ops.sh
```

This read-only check verifies the remote Hermes gateway, loopback listener, `/v1/models` probe, bridge environment, and recent Hermes warning/error log signal without printing gateway tokens.

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
