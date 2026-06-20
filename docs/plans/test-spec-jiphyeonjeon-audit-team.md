# Test Spec — 집현전-감사팀 Integrated Audit Team

## Verification goal
집현전-감사팀 Phase 1 구현이 read-only by default, append-only opt-in, no-secret, no-remediation, no-publish 제약을 지키면서 six audit suites의 주요 failure/degraded signals를 재현 가능하게 검출하는지 검증한다.

## Unit tests: `test_audit_ops_unittest.py`
1. **Schema**
   - every issue contains `schema_version`, `issue_id`, `team_id`, `suite`, `signal`, `severity`, `observed_at`, `evidence_refs`, `recommended_action`, `no_mutation=true`.
   - `issue_id` is deterministic for same suite/signal/evidence and changes for different evidence.
2. **Default no-write**
   - CLI/digest builder with default args writes no files in temp workspace.
   - stdout JSON has digest and issues only.
3. **Append-only write**
   - `--write-issue-queue` appends new issue once.
   - second run with same issue appends zero duplicate rows.
   - existing rows are not rewritten.
4. **Secret redaction**
   - fake Discord token, webhook URL, OpenAI-style key, relay token, API key do not appear in stdout or JSONL.
5. **`schedule_cron_drift`**
   - committed wrapper missing -> error.
   - wrapper syntax invalid fixture -> error.
   - stale last-status/log > SLA -> warning.
   - matching wrapper/status -> no issue.
6. **`discord_liveness_log_lag`**
   - stale ready/status/log metadata -> warning.
   - explicit service error metadata -> error.
   - no test claims true event-loop lag without heartbeat artifact.
7. **`scheduled_backlog_sla`**
   - pending count threshold breach -> warning.
   - oldest pending age threshold breach -> warning.
   - decided rows excluded from pending count.
8. **`trust_gate_incidents`**
   - card-news trust gate block summary emits protected block issue with reason code.
   - repeated same reason over threshold escalates to warning.
   - no command/action suggests approval, retry publish, or bypass.
9. **`provenance_schema`**
   - missing provenance/schema fields -> error.
   - generated wiki page lacking unreviewed-generated marker -> warning/error according to artifact type.
10. **`api_rate_budget`**
   - provider 429 + fallback/report success -> warning `degraded`.
   - provider 429 causing no report/status failure -> error.
   - normal provider summary -> no issue.

## Manifest tests: `tests/test_runtime_manifests.py`
- `jiphyeonjeon-audit-team-digest` exists in `runtime/jobs.yaml`.
- `jiphyeonjeon-audit-team` exists in `runtime/agents.yaml`.
- audit job `type` is health-check/local-advisory/read-only equivalent.
- audit command refs include `discord-openclaw-audit-team`.
- audit command refs do **not** include:
  - `systemctl start`, `systemctl restart`
  - `crontab`, cron installer scripts
  - deploy scripts
  - Discord post/publish CLIs
  - approve/reject/hold review commands
- audit agent boundaries include no approval, no mutation, no publishing, no secret values.
- manifest secret scanner still rejects concrete secret values.

## Regression tests
- Existing Guard tests: `python -m pytest skills/discord-openclaw-bridge/project/tests/test_guard_ops_unittest.py` or current project test runner equivalent.
- New audit tests: `python -m pytest skills/discord-openclaw-bridge/project/tests/test_audit_ops_unittest.py`.
- Runtime manifests: `python3 -m unittest tests.test_runtime_manifests`.
- If package import path requires uv: run from `skills/discord-openclaw-bridge/project` with `uv run pytest ...`.

## Manual verification / review checklist
- Run `discord-openclaw-audit-team` against fixture workspace and confirm no files are created.
- Run with `--write-issue-queue` in temp dir and confirm append-only rows.
- Inspect sample digest for sanitized evidence only.
- Confirm trust gate block text says protected/manual review, not failure-to-publish requiring bypass.
- Confirm Phase 1 docs state Discord event-loop lag is inferred from freshness only; true lag metric is Phase 2.

## Stop condition
Implementation is ready for execution handoff only when unit tests, manifest tests, secret redaction tests, and no-remediation negative tests pass, and docs preserve the Phase 1 safety boundary.

## Architect Iteration Test Addendum

### Audit log tests
- `--write-audit-log` writes rows with `event_id`, `team_id`, `suite`, `signal`, `severity`, `observed_at`, `snapshot_observed_at`, `evidence_refs`, `issue_id`, `result`, `redaction_applied=true`, `no_mutation=true`.
- Audit log is append-only and preserves repeated observations; second run may add a new audit event while issue queue remains deduplicated.
- Existing audit log rows are not rewritten or reordered.
- Fake secrets are absent from both issue queue and audit log rows.

### Snapshot freshness tests
- Missing configured snapshot emits `snapshot_missing`.
- Stale snapshot with `snapshot_observed_at` older than threshold emits `snapshot_stale`.
- Malformed timestamp emits `snapshot_unparseable`.
- Fresh snapshot permits suite-specific checks to proceed.
- Audit CLI tests must not execute live `crontab`, `systemctl`, deploy, restart, or Discord posting commands.

### Provenance/schema matrix tests
- Fixture per artifact type verifies required fields and forbidden fields listed in the PRD matrix.
- Newsletter candidate with `publish_ready=true` fails Phase 1 provenance check.
- Generated wiki page missing `trust_status: "unreviewed-generated"` emits the expected warning/error.
- Runtime manifest audit entry containing concrete secret values or remediation commands fails scoped checks.

### Repeated trust-gate incident tests
- Three identical block dedup keys within seven days emits repeated-block warning.
- Two identical blocks within seven days does not escalate beyond base block issue.
- Three blocks outside seven days do not trigger repeated warning.
- Missing timestamps produce snapshot freshness/unparseable signals instead of repeated-block escalation.

### Scoped manifest negative-gate tests
- Denylist assertions apply only to `jiphyeonjeon-audit-team-digest` and `jiphyeonjeon-audit-team`.
- Existing non-audit jobs containing cron installers, Discord publish commands, or ops service checks remain valid.
- `scripts/check-runtime-manifests.py` required job/agent sets include the audit team ids.
