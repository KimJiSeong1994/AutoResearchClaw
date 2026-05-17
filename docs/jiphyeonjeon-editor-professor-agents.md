# PRD: Jiphyeonjeon Editor and Advisor Agents

## Decision
Add two read-only advisory runtime control-plane agents for Auto Research trust hardening:

1. `jiphyeonjeon-editor` / **집현정-편집자** — cross-surface canonical identity and dedupe reporting.
2. `jiphyeonjeon-advisor` / **집현전-지도교수** — evidence coverage, source diversity, citation-quality, and overclaim gate.

Keep the Human Review / Promotion Coordinator explicitly **pending**. It must not become an active runtime agent or job in this phase.

## Naming note
`집현정-편집자` preserves the user-provided Korean display name exactly, even though other project agents use the `집현전-*` prefix. The stable runtime ID remains ASCII/lowercase as `jiphyeonjeon-editor`.

## Drivers
1. Auto Research needs a cross-pipeline trust layer before adding more collection volume.
2. Duplicate detection must span Miner, Traveler, newsletter archive, paper recommender, card-news, and wiki-adjacent artifacts.
3. Evidence and citation quality should be checked independently from writers/publishers.
4. The first implementation must be local, read-only, testable, and safe before any production/Discord side effects.

## Scope
### 1. 집현정-편집자 / Canonical Identity
- Read one or more JSON/JSONL artifacts.
- Produce canonical keys from DOI, arXiv, OpenReview, sanitized URL, or normalized title fallback.
- Report duplicate groups across inputs.
- Emit `agent_id`, `agent_name`, `input_counts`, `item_count`, `canonical_count`, `duplicate_group_count`, `duplicate_groups`, `no_mutation`.
- Forbidden: rewriting source queues, approving/rejecting content, mutating publication history, or publishing.

### 2. 집현전-지도교수 / Evidence Quality Gate
- Read a JSON, JSONL, or Markdown research/publication artifact.
- Compute evidence URL count, source-domain diversity, row-level evidence coverage, possible overclaim language, and quality status.
- Emit `quality_status=pass|needs_review|fail`, `quality_gate_passed`, `evidence_coverage_pct`, `source_diversity`, `issues`, `no_mutation`.
- Forbidden: generating prose, approving promotion, posting to Discord, or modifying source artifacts.

### 3. Pending promotion coordinator
- Record `editorial-promotion-coordinator` as a downstream pending concept only.
- Do not add a runtime agent/job for it in this phase.
- Future phase may consume Editor/Advisor verdicts to drive `needs_editorial_review -> publish_queue -> published`.

### 4. Runtime/governance integration
- Add both agents and jobs to `runtime/agents.yaml` and `runtime/jobs.yaml`.
- Add required IDs to `scripts/check-runtime-manifests.py` and tests.
- Keep command refs pointing to existing local scripts only.

## Acceptance criteria
- `집현정-편집자` script reports duplicate groups without mutating input files.
- `집현전-지도교수` script distinguishes pass/fail/needs_review based on evidence coverage and source diversity.
- Runtime manifests validate and contain both new agents/jobs.
- Promotion coordinator remains absent from active runtime manifests.
- Prompt governance remains valid.
- Existing unrelated Traveler staged changes are not modified or reverted.

## Non-goals
- No Discord posting.
- No cron install.
- No automatic approval/promotion workflow.
- No new external dependencies.
