# PRD — 집현전-감사팀 Integrated Audit Team

## 목표
AutoResearchClaw의 분산된 모니터링/가드 기능을 **집현전-감사팀**이라는 read-only fan-in 감사 계층으로 통합한다. 감사팀은 기존 집현전-경비원(Guard), Review Optimizer, Editor, Advisor, Candidate Orchestrator를 대체하지 않는다. 각 owner의 산출물과 runtime manifest를 읽어 silent drift, backlog, trust gate block, provenance/schema, API rate-limit 신호를 operator-facing issue digest로 정규화한다.

## 범위
### Phase 1 포함
- 새 CLI `discord-openclaw-audit-team` 기본 stdout-only digest.
- 명시 flag에서만 append-only JSONL 쓰기: `--write-issue-queue`, `--write-audit-log`.
- narrow suite registry:
  1. `schedule_cron_drift`
  2. `discord_liveness_log_lag` *(Phase 1은 log/status freshness inference only; 실제 event-loop lag metric 아님)*
  3. `scheduled_backlog_sla`
  4. `trust_gate_incidents`
  5. `provenance_schema`
  6. `api_rate_budget`
- `runtime/jobs.yaml`, `runtime/agents.yaml`에 read-only audit job/agent 선언.
- README/docs에 manual runbook과 phase boundary 명시.

### Phase 1 제외
- 자동 승인, 자동 발행, trust gate 우회.
- `systemctl start/restart`, deploy, cron install/update, 파일 재배치 같은 remediation.
- Discord/public posting. Phase 1 audit job은 로컬/원격 artifact read 및 optional append-only audit write만 수행.
- private body, token 값, raw secret 출력.

## RALPLAN-DR
### Principles
1. **No god agent**: 감사팀은 owner별 suite 결과를 모으는 fan-in이며 승인/복구/발행 권한이 없다.
2. **Read-only by default**: 기본 실행은 파일 생성 없이 stdout digest만 출력한다.
3. **Append-only accountability**: 쓰기가 필요할 때도 별도 issue/audit JSONL에 append만 하며 기존 row를 rewrite하지 않는다.
4. **Trust gates are authoritative**: Editor/Advisor/Claw gate block은 우회 대상이 아니라 감사 대상이다.
5. **No secret leakage**: evidence는 sanitized path/id/hash/reason code만 포함한다.

### Top decision drivers
1. 최근 newsletter/card-news 및 Traveler 장애는 cron이 남아도 committed wrapper가 사라지는 **scheduler drift/silent failure**였다.
2. 기존 Guard는 miner-seeds, Traveler handoff, seed errors, review backlog 중심이라 newsletter/card-news, provenance/schema, rate-limit, Discord liveness를 충분히 포괄하지 못한다.
3. `runtime/jobs.yaml`/`runtime/agents.yaml`가 이미 owner와 safety boundary를 선언하므로 manifest-aware audit가 자연스럽다.

### Viable options
| Option | 장점 | 단점 | 판정 |
|---|---|---|---|
| A. `guard_ops.py` 확장 | 빠르고 기존 issue queue 패턴 재사용 쉬움 | Guard가 광역 god agent화; Miner/Traveler guard 책임과 전체 감사 책임이 섞임 | Reject for Phase 1 architecture; helper/schema는 재사용 가능 |
| B. 별도 `audit_ops.py` + narrow suite registry | 경계 명확, suite별 테스트 가능, 기존 owner 보존 | 새 manifest/job/test 필요 | **Chosen** |
| C. 외부 observability/cron monitor 도입 | 장기적으로 정확한 heartbeat/lag/alert 가능 | 새 의존/운영면, Phase 1 no mutation/read-only 제약 초과 | Phase 2+ 후보 |
| D. Auto-remediator/redeploy agent | 복구 속도 빠름 | phase 1 제약 위반; cron/redeploy mutation 및 overreach 위험 | Explicitly rejected |

### Chosen architecture
`skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/audit_ops.py`에 suite registry와 공통 issue schema를 둔다. 기존 `guard_ops.py`는 유지하고, 필요하면 `ops_issue.py` 같은 shared helper로 deterministic issue id / append-only writer만 추출한다. `jiphyeonjeon-audit-team` agent는 runtime manifest상 team/fan-in agent로 선언하되 owns_job은 `jiphyeonjeon-audit-team-digest` 하나로 제한한다.

## 공통 audit issue schema
필수 필드:
- `schema_version`: `1`
- `issue_id`: `audit_` + hash(`suite`, `signal`, sanitized evidence key)
- `team_id`: `jiphyeonjeon-audit-team`
- `suite`
- `signal`
- `severity`: `info|warning|error`
- `observed_at`: UTC ISO8601
- `evidence_refs`: sanitized paths, artifact ids, reason codes, hashed URLs only
- `recommended_action`: manual operator action only
- `no_mutation`: `true`

금지: token/env value, raw Gmail body, private newsletter body, Discord message body, webhook URL, API key, auto-remediation command.

## Phase 1 suite contract
| Suite | Read sources | Signals / severity | Non-goals |
|---|---|---|---|
| `schedule_cron_drift` | `runtime/jobs.yaml`, committed wrappers under `scripts/`, known runner paths, optional captured `crontab -l` text/status artifacts | missing committed wrapper=`error`; wrapper bash syntax invalid=`error`; last status/log older than SLA=`warning`; cron target absent from provided snapshot=`warning` | no cron edit/install |
| `discord_liveness_log_lag` | service status artifacts/log freshness from existing status scripts or sanitized log metadata | ready/log heartbeat stale=`warning`; service status missing=`warning`; explicit error exit in log metadata=`error` | no real event-loop lag claim until Phase 2 heartbeat instrumentation |
| `scheduled_backlog_sla` | review queue JSONL, decision JSONL, candidate queue/status artifacts | pending age/count SLA breach=`warning`; scheduled job backlog artifact absent=`warning` | no approve/reject/hold |
| `trust_gate_incidents` | `reports/jiphyeonjeon-trust-gates/*-summary.json`, card-news publication audit JSONL | gate block reason codes=`info|warning`; repeated same block over N runs=`warning`; malformed summary=`error` | no gate bypass/retry/publish |
| `provenance_schema` | newsletter/raw items, manual link provenance fields, wiki provenance outputs, runtime manifests | missing required provenance/schema fields=`error`; unreviewed-generated marker absent for generated wiki pages=`warning/error` per artifact type | no artifact rewrite |
| `api_rate_budget` | provider status JSON/log summaries for arXiv/Semantic Scholar/YouTube/OpenClaw gateway | 429 with fallback success=`warning:degraded`; repeated 429 over window=`warning`; provider failure causing no report=`error` | no key rotation/throttling mutation in Phase 1 |

## Artifacts
- Code: `audit_ops.py`; optional shared helper `ops_issue.py`.
- CLI entrypoint: `discord-openclaw-audit-team` in `skills/discord-openclaw-bridge/project/pyproject.toml`.
- Runtime manifest: `runtime/jobs.yaml` job `jiphyeonjeon-audit-team-digest`; `runtime/agents.yaml` agent `jiphyeonjeon-audit-team`.
- Audit outputs: stdout digest; optional `~/.openclaw/workspace/review/jiphyeonjeon-audit/issues.jsonl`; optional `~/.openclaw/workspace/logs/jiphyeonjeon-audit/audit-log.jsonl`.
- Docs: `skills/discord-openclaw-bridge/README.md` audit section and/or `docs/ops/jiphyeonjeon-audit-team.md`.
- Tests: `skills/discord-openclaw-bridge/project/tests/test_audit_ops_unittest.py`, `tests/test_runtime_manifests.py` updates.

## Code touchpoints
1. `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/audit_ops.py` — suite registry, digest builder, CLI parser.
2. `skills/discord-openclaw-bridge/project/src/discord_openclaw_bridge/guard_ops.py` — avoid monolith growth; only share helper if needed.
3. `skills/discord-openclaw-bridge/project/pyproject.toml` — script entrypoint.
4. `runtime/jobs.yaml` — read-only health-check job; command refs must not include install/deploy/restart/post.
5. `runtime/agents.yaml` — boundaries: no approval, no mutation, no publish, no secret values.
6. `scripts/check-runtime-manifests.py` and `tests/test_runtime_manifests.py` — required ids and negative command/safety assertions.
7. README/docs ops runbook.

## Phases
### Phase 1 — Audit digest and safety rails
- Build CLI with fixture-first suites.
- Add manifest entries and negative manifest checks.
- Document manual runbooks for each issue signal.
- Stop after local tests pass; no production cron mutation.

### Phase 1.5 — Operator reporting without remediation
- Add optional sanitized report rendering for operations forum only if explicitly requested in a later plan.
- Keep trust gate blocks as protected state, not failures.

### Phase 2 — Instrumentation for true liveness/lag
- Add sanitized heartbeat artifacts for bridge/Miner/Traveler event loop lag and last handled event timestamps.
- Keep restart/redeploy as manual-only unless a later approved plan changes the safety model.

## Risks and mitigations
- **God-agent drift**: suite registry reports only; owner agents keep decisions. Manifest tests reject publish/restart/install commands.
- **False confidence**: digest `ok` means “covered signals clear,” not “safe to publish.” Docs and schema include `no_mutation=true`.
- **False positives on rate limits**: classify 429+fallback success as degraded warning, not fatal.
- **Secret leakage**: redaction tests include fake tokens/webhooks/API keys.
- **Issue noise**: deterministic issue ids and append-once semantics prevent duplicate spam.
- **Phase creep**: event-loop lag is inference-only until Phase 2 heartbeat artifact exists.

## Acceptance criteria
- `discord-openclaw-audit-team` 기본 실행은 stdout digest만 만들고 파일을 생성하지 않는다.
- optional write flags는 append-only JSONL만 쓰며 기존 row를 rewrite하지 않는다.
- issue schema 필수 필드와 `no_mutation=true`가 모든 issue에 존재한다.
- six suites have fixture tests for positive and non-issue cases.
- scheduler drift, backlog SLA, trust gate block, provenance/schema error, provider 429 degraded 상태가 재현 가능하다.
- `runtime/jobs.yaml`/`runtime/agents.yaml`에 audit job/agent가 있고 Phase 1 command_refs에 restart/redeploy/cron mutation/publish가 없다.
- fake secrets are absent from stdout and JSONL.
- existing Guard tests and runtime manifest tests still pass.

## ADR
Decision: 별도 `audit_ops.py` + `jiphyeonjeon-audit-team` manifest fan-in을 채택한다.
Drivers: scheduler drift coverage, owner boundary preservation, read-only safety.
Alternatives: Guard 확장(rejected for god-agent risk), 외부 observability(defer), auto-remediator(rejected).
Consequences: 새 CLI/test/manifest 작업이 필요하지만 Guard 책임은 작게 유지된다.
Follow-ups: Phase 2 heartbeat instrumentation, optional report renderer, external monitor evaluation.

## Architect Iteration Addendum — Required clarifications

### Audit log row schema and duplicate policy
`--write-audit-log` writes an append-only operational observation log separate from the deduplicated issue queue.

Required audit log row fields:
- `schema_version`: `1`
- `event_id`: `audit_event_` + hash(`suite`, `signal`, `observed_at`, normalized evidence key)
- `team_id`: `jiphyeonjeon-audit-team`
- `suite`
- `signal`
- `severity`
- `observed_at`: UTC ISO8601
- `snapshot_observed_at`: UTC ISO8601 or `null` for purely local manifest checks
- `evidence_refs`: sanitized paths, artifact ids, reason codes, hashed URLs only
- `issue_id`: matching deterministic issue id when the event corresponds to an issue, else `null`
- `result`: `ok|degraded|issue|skipped`
- `redaction_applied`: `true`
- `no_mutation`: `true`

Duplicate policy:
- Issue queue deduplicates by deterministic `issue_id` to prevent repeated operator spam.
- Audit log does **not** deduplicate by `issue_id`; repeated observations are allowed to preserve trend/history.
- Audit log still must be append-only and must never rewrite existing rows.

### Suite input snapshot freshness contract
Every suite that reads captured external status/log/crontab/provider artifacts must treat the artifact as a snapshot with explicit freshness metadata.

Required snapshot metadata when applicable:
- `snapshot_observed_at`: UTC ISO8601 timestamp supplied by the producer or derived from file mtime only when documented.
- `source_path`: sanitized repo-relative or workspace-relative path; no secret-bearing URL/env value.
- `max_snapshot_age_seconds`: suite-specific threshold.
- `snapshot_source`: e.g. `status_script`, `cron_snapshot`, `service_log_summary`, `trust_gate_summary`, `provider_status`.

Signals:
- `snapshot_missing`: warning when an optional but configured artifact is absent.
- `snapshot_stale`: warning when `observed_at - snapshot_observed_at > max_snapshot_age_seconds`.
- `snapshot_unparseable`: error when required metadata is malformed.

Phase 1 live-command boundary:
- Audit suites consume committed manifests and already-captured artifacts/summaries.
- They do not run `crontab`, `systemctl`, deployment, restart, or Discord posting commands themselves.
- A separate operator/status script may generate snapshots outside this audit CLI, but the audit CLI remains read-only by default.

### Provenance/schema required-field matrix
| Artifact type | Required fields / markers | Forbidden fields / notes |
|---|---|---|
| Miner intake row | `intake_id`, `url`, `source`, `created_at` or equivalent timestamp, sanitized Discord metadata ids when present | token values, raw private message body |
| Claw decision row | `decision_id` or deterministic decision key, `intake_id`/URL link, `decision` in `approve|reject|hold`, `reviewed_at`, `reviewer` | rewriting prior decisions |
| Approved manual link row | `url`, approval provenance linking back to decision/intake when available, title/source metadata | unapproved pending queue rows |
| Newsletter candidate row | `candidate_id`, `candidate_status=needs_editorial_review`, `url`, `provenance` or `approved_decision_ref` | `publish_ready=true` in Phase 1 candidates |
| Trust gate summary | `decision`, `reason_codes`, `summary_path` or generated report id, `generated_at` | raw secret values or private bodies |
| Card-news publication audit row | `decision`/`status`, `surface=card-news` when available, timestamp, sanitized reason codes | raw unpublished private content |
| Generated wiki page | visible/generated provenance marker such as `trust_status: "unreviewed-generated"` when generated and unreviewed | pretending generated content is human-reviewed |
| Runtime manifest job/agent | `id`, `owner_agent`/agent `id`, command refs, safety/boundaries | concrete secret values, remediation commands in audit job |

### Repeated trust-gate incident window
Source of truth for repeated trust-gate block trend:
1. Prefer `card-news-publication-audit.jsonl` rows containing trust gate block/failure decisions when present.
2. Fall back to `reports/jiphyeonjeon-trust-gates/*-summary.json` ordered by `generated_at` or file mtime only when `generated_at` is unavailable.

Dedup key:
- `surface + decision + sorted(reason_codes) + artifact_hash_or_summary_path_hash`.

Window:
- Phase 1 default: warning when the same dedup key appears at least `3` times within `7` days.
- If timestamps are missing or stale, emit `snapshot_unparseable`/`snapshot_stale` instead of repeated-block escalation.

### Scoped manifest negative gate
Manifest denylist checks apply only to:
- `runtime/jobs.yaml` entry `jiphyeonjeon-audit-team-digest` command refs and safety fields.
- `runtime/agents.yaml` entry `jiphyeonjeon-audit-team` boundaries/responsibilities.

They must not be applied globally because other existing jobs legitimately install cron, post to Discord, deploy, or inspect services. Update both `scripts/check-runtime-manifests.py` required ids and `tests/test_runtime_manifests.py` scoped assertions when implementing.
