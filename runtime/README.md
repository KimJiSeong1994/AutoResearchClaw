# Runtime control-plane manifests

Phase 1 records the runtime inventory and validates it before workspace deployment.

- `jobs.yaml` lists current operator, cron, service, relay, and review jobs referenced by the repository README and skills.
- `agents.yaml` lists the agents/services responsible for those jobs and their operational boundaries.

These manifests are intentionally descriptive: they do not execute jobs yet, but `scripts/deploy-openclaw-workspace.sh` now blocks on `scripts/check-runtime-manifests.py` so broken ownership/cross-reference state does not deploy.

## Shape conventions

Top-level fields:

- `version`: manifest schema version. Current value: `1`.
- `kind`: either `runtime-jobs` or `runtime-agents`.
- `metadata`: manifest name, source scope, and rollout phase.
- `defaults`: shared runtime defaults such as EC2 workspace and loopback gateway.
- `jobs` / `agents`: inventory entries.

Entry conventions:

- `id` is stable, lowercase, and dash-separated.
- `command_refs` are references to existing commands only; do not add scripts from this manifest layer.
- `owner_agent` in `jobs.yaml` should match an `agents.yaml` `id`.
- `owns_jobs` in `agents.yaml` should reference `jobs.yaml` entries.
- `safety` / `boundaries` capture non-secret, operator-facing guardrails.

## Suggested validation

```bash
python3 scripts/check-runtime-manifests.py
python3 -m unittest tests/test_runtime_manifests.py
```
