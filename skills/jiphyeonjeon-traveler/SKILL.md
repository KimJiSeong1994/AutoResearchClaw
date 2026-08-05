---
name: jiphyeonjeon-traveler
description: Use this runtime skill to evaluate and govern Jiphyeonjeon Traveler paper search, source discovery, provider fallback, daily collection reporting, and SkillOpt operational alignment on Hermes.
---

# jiphyeonjeon-traveler

## Trigger

Use this runtime skill when 운영자가 집현전-여행자의 논문 검색, source discovery,
daily collection report, provider fallback, or SkillOpt 운영 정합성을 점검하거나
개선하려고 할 때 적용한다. The skill governs the Traveler operational loop, not
general blog writing or reporter article drafting.

## Input Contract

Required runtime evidence is read-only JSON snapshot material:

- Scout status: `~/.hermes/workspace/state/traveler-scout-last-status.json`
- Source discovery status:
  `~/.hermes/workspace/state/traveler-source-discovery-last-status.json`
- Collection report status:
  `~/.hermes/workspace/state/traveler-collection-report-last-status.json`
- Miner review queue and append-only latest decisions:
  `~/.hermes/workspace/review/jiphyeonjeon-claw/link-review-queue.jsonl` and
  `link-review-decisions.jsonl`
- Traveler outcome ledger:
  `~/.hermes/workspace/state/traveler-outcome-ledger.jsonl`
- Workspace root metadata: `~/.hermes/workspace`

Optional context may include public `runtime/traveler-scout-topics.json`,
`runtime/traveler-scoring.json`, and sanitized provider summaries. Never require
raw Discord messages, private mailbox bodies, local vault paths, API keys, bot
tokens, or webhook URLs as inputs.

## Output Contract

<!-- SKILLOPT:TRAVELER:SEARCH-SCOPE:START -->
SkillOpt Traveler search-scope contract:

- Primary reward is the latest Miner/Claw exact-URL approval after Traveler
  handoff within the 7-day censor window, weighted 70%; a later reject/hold
  revokes the positive label.
- Exploration reward is approved-set Shannon diversity across provider, domain,
  and topic buckets within the same 7-day censor window, weighted 30%.
- Minimum eligible sample size is 5 newly evaluated Traveler candidates; below
  that, emit no-op/insufficient-sample and do not apply changes.
- Automatic tuning may edit only:
  - `runtime/traveler-scout-topics.json` fields `priority` and `max_candidates`
  - this marker-bounded generated section between
    `SKILLOPT:TRAVELER:SEARCH-SCOPE:START` and
    `SKILLOPT:TRAVELER:SEARCH-SCOPE:END`
- Automatic tuning must not edit any other files, keys, prompts, scoring
  thresholds, provider code, service units, cron, queues, Discord state, or
  PaperWiki artifacts.
- Every applied change must write a before/after hash, timestamped backup,
  rollback pointer, and append-only lineage row before reporting success.
- Rollback must restore the exact backed-up bytes for both allowed targets and
  must not infer state from generated prose.
<!-- SKILLOPT:TRAVELER:SEARCH-SCOPE:END -->

Emit operator-facing evidence with this shape:

- `schema_version`: `skillopt-traveler-ops.v1`
- `skill`: `jiphyeonjeon-traveler`
- `status`: one of `ok`, `degraded`, or `failed`
- `reasons`: deterministic reason codes
- `metrics`: accepted, duplicate, rejected, reviewed, provider error, stale
  pending, and report candidate counts
- `path_policy`: sanitized Hermes/OpenClaw path policy observations
- `freshness`: per-artifact `run_at` status, default max age 36 hours,
  and 5-minute future clock-skew tolerance
- `provider_errors`: sanitized provider failure summaries only
- `automatic_apply`: `false` for the read-only operational evaluator report;
  bounded auto-tune reports may describe an applied/no-op sink action under the
  generated search-scope contract when `TRAVELER_SKILLOPT_AUTOTUNE_ENABLED=1`
- `requires_reviewer_gate`: `true` for unrestricted SkillOpt apply; the bounded
  sink is pre-authorized only for the two generated search-scope targets above

## Workflow

Read-only evaluator:

1. Read the three status snapshots and workspace metadata.
2. Reject missing or malformed snapshots as `failed`.
3. Reject missing, invalid, stale, or excessively future `run_at` timestamps as
   `failed`; default staleness limit is 36 hours.
4. Reject production `~/.openclaw/workspace` paths as `failed`; Hermes paths are
   the production target.
5. Classify source-discovery outcomes:
   - accepted candidates and no provider errors: `ok`
   - accepted candidates with partial provider error/fallback: `degraded`
   - zero accepted after processing: at least `degraded`
   - zero accepted plus provider errors: `failed`
   - duplicates-only or stale-pending blocked: `degraded`
6. Save reports only under `.omx/reports/skillopt/` or an approved temporary
   path. In this evaluator step, do not mutate Traveler queues, scoring config,
   cron, systemd, Discord, PaperWiki, skill files, or topic files.

Bounded auto-tune sink:

7. Run only after the evaluator report exists, its `status` is not `failed`,
   and `traveler-skillopt-reward.v1` exists.
8. Use the generated search-scope reward contract above: 70% primary Miner
   exact-URL approval, 30% approved-set provider/domain/topic Shannon diversity,
   7-day censor, and minimum sample size 5.
9. Apply only the allowed `runtime/traveler-scout-topics.json`
   `priority`/`max_candidates` changes and this marker-bounded generated
   section; write hash, backup, rollback, and lineage evidence.

## Safety / Privacy

The evaluator is read-only and must sanitize:

- absolute local paths such as `/Users/...`
- Hermes/OpenClaw home workspace paths
- Discord webhooks, bot tokens, API keys, and OpenAI-style secret tokens
- private mailbox/body markers

Do not expose raw paper queue rows when a hash, count, provider name, or public
URL host-level summary is sufficient.

## Evidence Policy

Prefer deterministic snapshots and held-out fixtures over subjective judgement.
Operational SkillOpt evidence is sufficient only when the report includes the
status, reason codes, metrics, path policy, and privacy-clean serialized output.

## Verification

Run the focused gates before claiming this skill is operationally reflected:

```bash
python3 -m pytest tests/test_skillopt_traveler_ops.py tests/test_skillopt_eval.py
python3 scripts/skillopt_eval.py --root . --out .omx/reports/skillopt/skillopt-eval-latest.json
```

The expected SkillOpt harness includes exactly the `jiphyeonjeon-traveler`
held-out fixture set. The read-only evaluator report must keep
`automatic_apply=false`; bounded auto-tune reports are verified separately by
their allowed-target, hash, backup, rollback, and lineage evidence.

## Failure / Rollback

If SkillOpt reports `failed`, keep Traveler production unchanged and inspect the
snapshot reason codes first. Restoring OpenClaw is not an automatic SkillOpt
action; OpenClaw paths are rollback-only operator context and must not appear in
normal production snapshots.

Automatic tuning is allowed only for the two bounded targets named in the
generated search-scope contract. Unrestricted SkillOpt apply, scoring-threshold
mutation, and provider-code mutation remain forbidden.
