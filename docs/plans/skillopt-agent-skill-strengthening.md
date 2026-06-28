# SkillOpt Agent Skill Strengthening Plan

Status: implemented through Phase 3 proposal generation  
Owner: `skillopt-auditor` runtime agent  
Date: 2026-06-28

## Purpose

SkillOpt improves local Codex skills and runtime agent prompts through a gated, auditable loop:

1. audit skill and runtime metadata for missing contracts, weak verification, privacy risk, and runtime mapping gaps;
2. evaluate selected skills against held-out fixtures;
3. generate patch proposals as reviewable JSON candidates;
4. defer live mutation to a separate controlled apply phase.

## Runtime agent boundary

The `skillopt-auditor` agent is a control-plane audit agent. It may read committed skills, runtime manifests, and fixture data, and it may write reports under `.omx/reports/skillopt/`. It must not directly rewrite skill files during the audit/eval/proposal phase.

## Phase 3 implemented scope

- `scripts/skillopt_audit.py` creates readiness findings for skills and runtime mappings.
- `scripts/skillopt_eval.py` evaluates held-out fixtures for supported skills.
- `scripts/skillopt_propose.py` emits deterministic proposal JSON files and rejected-edit buffers.
- `runtime/agents.yaml` and `runtime/jobs.yaml` expose the SkillOpt audit/eval/proposal jobs.
- `tests/fixtures/skillopt/` and `tests/test_skillopt_*.py` lock regression behavior.

## Safety constraints

- Proposal generation is no-mutation.
- Private paths, token-like values, Discord webhooks, and private-only evidence must be rejected or sanitized.
- Rejected proposal buffers suppress repeated candidates unless explicitly included.
- Accepted lineage is not written in Phase 3; it belongs to a later controlled apply phase.

## Follow-up

Phase 4 should implement a controlled apply engine with one proposal at a time, dry-run first, reviewer and critic approval, hash-anchored baseline checks, before/after eval, rollback, and append-only accepted lineage.
