# SkillOpt Traveler Operationalization

Date: 2026-08-05

## Objective

Make Jiphyeonjeon Traveler paper/source discovery a first-class SkillOpt target.
Use exact-URL Miner approval as the primary downstream objective, preserve
provider/domain/topic diversity as a secondary objective, and allow unattended
tuning only inside the Traveler scout-topic search range and the generated
search-scope section of the dedicated Traveler skill document.

## Evidence-backed gaps

- `scripts/skillopt_eval.py` evaluates three non-Traveler skills, so Traveler
  proposals have no scoped held-out evidence.
- Traveler already emits useful telemetry (`requests_processed`,
  `reviewed_count`, `accepted_count`, duplicate/rejection/error counts,
  evidence counts, and provider results), but SkillOpt does not consume it.
- A discovery run can exit successfully with `accepted_count=0`; the daily
  report therefore cannot distinguish clean empty yield from degraded search.
- Hermes services inject correct paths, while several Traveler code defaults
  still point at the rollback-only OpenClaw workspace.
- The old outcome code treated Miner intake/review presence as adoption. That
  is too weak for the requested target: the latest explicit Miner/Claw
  `approve` decision for the exact URL is authoritative. The approved
  manual-link export remains a compatibility fallback when no decision-log row
  exists, so a missing or stale export cannot hide or resurrect a decision.
- `traveler_tuning` already provides baseline hash, backup, and append-only
  lineage primitives, but its former human-only drop-source policy does not
  implement the new bounded automatic search-range tuning requirement.

## Chosen architecture

Use an event-sourced reward adapter plus a bounded automatic tuning sink.

1. A stdlib-only operational evaluator reads Traveler status snapshots and
   emits a sanitized `skillopt-traveler-ops.v1` artifact.
2. Deterministic held-out fixtures exercise the same classification logic from
   the SkillOpt evaluation harness under the dedicated skill key
   `jiphyeonjeon-traveler`.
3. The outcome ledger records distinct `handed_off` and `miner_approved`
   events using the sanitized URL's 16-character SHA-256 `url_key`. Legacy
   `adopted` events remain readable but are not a strong reward label.
4. The primary reward is Miner exact-URL approval rate after a seven-day
   censoring window. Recent unapproved handoffs are not counted as negatives.
5. The secondary reward is normalized Shannon diversity across approved
   provider, domain, and topic distributions. The combined reward weights
   Miner approval at 70% and diversity at 30%.
6. Automatic tuning is allowed only when at least five eligible samples exist.
   It may change only `priority` and `max_candidates` in
   `runtime/traveler-scout-topics.json` and the marker-bounded generated section
   in `skills/jiphyeonjeon-traveler/SKILL.md`.
7. Every automatic write requires exact path/field allowlists, matching
   baseline hashes, backups, post-write validation, rollback on failure, and
   append-only lineage. It cannot delete a topic or reduce projected diversity.
8. Outcome, evaluator, or tuning failures remain visible but cannot overwrite
   the main daily report exit code.

## Operational classification

- `ok`: candidates were accepted and no provider/path/freshness failure is
  present.
- `degraded`: useful output exists with partial provider failure, or a processed
  run produced only duplicates/rejections/stale-blocked work.
- `failed`: status input is missing/malformed, a production path resolves to
  rollback-only OpenClaw state, or a processed zero-acceptance run also reports
  provider/discovery errors.

The operational evaluator remains read-only. The separate bounded auto-tuning
sink must never relax evidence gates, add unapproved sources, edit queues,
change providers/credentials, mutate cron/systemd, or touch text outside the
Traveler skill document's generated search-scope markers.

## Acceptance criteria

- SkillOpt evaluation contains at least eight Traveler held-out cases.
- An anomaly case passes only when the evaluator correctly labels the anomaly;
  `accepted_count=0` is never silently reported as healthy after work was
  processed.
- Traveler proposals receive Traveler-scoped evaluation coverage rather than
  unrelated SkillOpt scores.
- Default Traveler paths resolve under `~/.hermes/workspace`; explicit legacy
  environment variables remain rollback-only compatibility.
- Miner approval joins use exact sanitized URL hashes and the latest append-only
  decision wins; intake/review presence and host overlap do not count as
  approval. A later reject/hold records a revocation event and removes that URL
  from the positive reward set.
- Reward output reports eligible, approved, censored, duplicate, per-axis
  diversity, dominance, and combined basis-point scores.
- Fewer than five eligible samples produces a successful no-op, not a write.
- The Hermes daily runner writes the operational, reward, auto-tune, and
  lineage artifacts without mutating queues or changing its primary report
  exit result.
- Automatic writes are restricted to the two allowlisted surfaces and are
  demonstrably hash-anchored, backed up, post-validated, and rollback-safe.
- Runtime manifests declare the evaluator, reward target, diversity policy,
  auto-tune allowlist, and forbidden side effects.
- Targeted tests, full repository tests, syntax checks, and EC2 read-only smoke
  validation pass before completion.

## Rollback

- Disable `TRAVELER_SKILLOPT_AUTOTUNE_ENABLED` to stop automatic writes while
  retaining outcome/reward visibility.
- Restore the versioned backups for the topic config and generated skill
  section; lineage identifies before/after hashes and rollback state.
- Explicit `OPENCLAW_WORKSPACE` and queue/status environment overrides remain
  available for rollback-only deployments.
- Queue, evidence, approval, Discord, systemd, and credential state are never
  auto-tuning targets and therefore require no rollback.
