# PROMPT_GOVERNANCE.md - Jiphyeonjeon-Claw Prompt Operations

Jiphyeonjeon-Claw owns prompt operations as part of service management and
reporting. Treat prompts, renderers, and report templates as governed service
assets rather than incidental strings.

## Control objective

- Keep one deployable inventory of prompt surfaces.
- Make every prompt change reviewable, testable, and rollbackable.
- Report prompt health together with service health.
- Preserve OpenClaw loopback, Discord, Gmail, and token privacy boundaries.

The machine-readable registry lives at `PROMPT_REGISTRY.json` in this workspace.

## Prompt lifecycle

Every prompt or renderer change must move through this lifecycle:

1. **Inventory** - identify `prompt_id`, owner, source path, input data classes,
   forbidden data, output contract, tests, metrics, and rollback knob.
2. **Draft** - make the smallest prompt or template change that satisfies the
   reporting goal.
3. **Review** - confirm the change does not weaken evidence, privacy, loopback,
   or output-format contracts.
4. **Evaluate** - run golden fixtures, schema checks, evidence checks, and
   no-secret checks before deployment.
5. **Deploy** - deploy through the existing workspace or skill deployment
   scripts; do not patch production prompts by hand.
6. **Monitor** - publish the unified prompt status schema with the service
   report.
7. **Rollback** - revert to the previous prompt version or disable the affected
   report path using the registry rollback notes.

## Change gate

Before deployment, a prompt change must pass:

- `python3 scripts/check-prompt-governance.py`
- output contract fixture tests for the affected prompt
- evidence provenance checks for research/reporting prompts
- no-secret checks for tokens, raw Gmail bodies, webhook URLs, OAuth values,
  gateway tokens, and private workspace links
- a post-deploy health check when the change affects OpenClaw, Discord, Gmail,
  or scheduled reporting

If a live EC2, Discord, Gmail, or Apps Script check needs credentials, record the
gap instead of printing secrets.

## Unified prompt status schema

Jiphyeonjeon-Claw reports prompt health with these fields:

| Field | Meaning |
| --- | --- |
| `run_id` | Stable id for the reporting run. |
| `run_at_utc` | UTC timestamp for the run. |
| `pipeline` | Reporting path, for example `daily_research`, `weekly_report`, `discord_card_news`, or `apps_script_relay`. |
| `prompt_version` | Registry version or prompt-specific revision used by the run. |
| `model_primary` | Primary model configured for the prompt call. |
| `model_fallback` | Fallback model set, if any. |
| `fallback_used` | Whether a model, source, or renderer fallback was used. |
| `fallback_reason` | Why fallback was used. |
| `source_stats` | Counts and classes of sources considered. |
| `candidate_count` | Candidate items before selection or clustering. |
| `query_count` | Queries generated or executed. |
| `cluster_count` | Clusters used for trend or recommendation output. |
| `evidence_coverage_pct` | Percent of selected claims with source evidence. |
| `min_evidence_per_cluster` | Lowest evidence count across emitted clusters. |
| `score_stats` | Rerank or selection score summary. |
| `prompt_input_bytes` | Size of input context passed to the prompt. |
| `prompt_output_valid_json` | Whether strict JSON prompts returned parseable JSON. |
| `secret_scan_pass` | Whether output and deployable prompt text passed secret checks. |
| `soul_source` | Source of the SOUL/persona state used for the run. |
| `soul_fallback_used` | Whether SOUL/persona fallback was used. |
| `soul_card_sha256` | Hash of the SOUL/persona card used for traceability. |
| `delivery_target` | Destination such as Obsidian, Discord, local report, or Apps Script relay. |
| `delivery_message_count` | Messages or artifacts delivered. |
| `artifact_dir` | Local or remote artifact directory. |
| `raw_path` | Raw/provenance artifact path when available. |
| `health_status` | `ok`, `warn`, or `fail`. |

## Reporting format

Operational reports should separate:

1. **Evidence** - source counts, validation output, health checks, and delivery
   state.
2. **Interpretation** - what changed and why it matters.
3. **Action** - rollback, fixture update, prompt review, or production follow-up.

## Rollback rule

Rollback is preferred over hot-patching when a prompt causes:

- invalid JSON or broken markdown contract output
- evidence coverage below the accepted threshold
- secret or private-data exposure
- Discord delivery failure from prompt size or format regression
- OpenClaw gateway safety boundary regression

Use the prompt-specific rollback notes in `PROMPT_REGISTRY.json` first, then
re-run the change gate.
