# EC2 CI/CD via GitHub Actions

This repository uses `.github/workflows/ec2-deploy.yml` to validate changes and deploy the OpenClaw workspace plus Discord bridge to EC2.

## Triggers

- `pull_request` to `main`: validation only.
- `push` to `main`: validation, then workspace deploy, Discord bridge deploy, and bridge reinstall/restart when EC2 production secrets are configured.
- `workflow_dispatch`: manual deploy with booleans for workspace, Discord bridge, and restart.

## Required GitHub environment

Create a GitHub Actions environment named `production` and add these as **environment secrets**. Do not put production EC2 credentials in broader repository secrets unless there is a documented emergency exception.

- `EC2_REMOTE_HOST`: SSH target such as `ubuntu@52.79.96.56`.
- `EC2_SSH_PRIVATE_KEY`: dedicated CI deploy key whose public key is authorized on the EC2 host. Do not reuse a personal laptop key.
- `EC2_KNOWN_HOSTS`: pinned SSH host key line for the EC2 host, matched to the exact host/IP in `EC2_REMOTE_HOST`.

Configure the `production` environment with required reviewers and branch protection for `main` before enabling automatic production deployment.

If any of `EC2_REMOTE_HOST`, `EC2_SSH_PRIVATE_KEY`, or `EC2_KNOWN_HOSTS` is missing, the workflow keeps validation green and skips the EC2 deploy steps with an explicit warning. This prevents credential setup gaps from blocking unrelated validation while still making deployment readiness visible.

Generate `EC2_KNOWN_HOSTS` only after verifying the host identity out of band. `ssh-keyscan` is only a collection tool; verify the fingerprint through AWS console/SSM, an existing trusted SSH session, or your infrastructure inventory before storing it:

```bash
ssh-keyscan -H 52.79.96.56
```

When the EC2 instance, public IP, or host key changes, rotate `EC2_KNOWN_HOSTS` in the same reviewed change window as the host migration.

Do not commit private keys, `.env` files, Discord tokens, OpenClaw gateway tokens, or GitHub secret values.

## Deploy key rotation and revocation

1. Generate a dedicated CI keypair outside the repository.
2. Add only the public key to the EC2 user's `authorized_keys`.
3. Store only the private key in the `production` environment secret `EC2_SSH_PRIVATE_KEY`.
4. Rotate the key on a scheduled cadence and immediately after any suspected exposure.
5. To revoke CI deploy access, remove the public key from EC2 and delete the GitHub secret.

## What deployment does

1. Runs prompt/runtime validators and tests.
2. Writes the private key to `$RUNNER_TEMP/ec2_deploy_key` with `0600` permissions.
3. Uses strict SSH host-key checking with the pinned `EC2_KNOWN_HOSTS` secret.
4. Runs `scripts/deploy-openclaw-workspace.sh`, which syncs:
   - `workspace/`
   - `skills/`
   - `runtime/`
   - `scripts/`
   The optional OpenClaw identity refresh is bounded by a remote timeout and only emits a warning if unavailable.
5. Runs `scripts/deploy-discord-openclaw-bridge.sh`.
6. Reinstalls/restarts `discord-openclaw-bridge.service` and prints sanitized `systemctl show` state only. Raw `journalctl` output is intentionally not emitted to GitHub Actions logs.

## Safety boundaries

- CI never stores secrets in the repository.
- `.env`, `.env.local`, `.env.production`, `.venv`, `__pycache__`, and `*.pyc` are excluded from rsync.
- `skills/`, `runtime/`, and `scripts/` are synchronized with `rsync --delete`; remote-only files under those directories can be removed. Keep operational secrets outside these synchronized paths or use the excluded filenames above.
- `집현정-편집자` and `집현전-지도교수` remain advisory-only; CI deployment does not create automatic content promotion.
- Production deployment is attached to the `production` environment so GitHub environment protection rules can require approval before deploy.

## Rollback and post-deploy checks

- Roll back by reverting the deploy commit on `main` or manually running the workflow from a known-good commit.
- After deploy, verify the workflow step reports `ActiveState=active`, `SubState=running`, and `ExecMainStatus=0`.
- If the bridge restart fails, inspect logs on EC2 directly over SSH; do not dump raw service logs into CI.

## Local parity checks

```bash
python3 scripts/check-runtime-manifests.py
python3 scripts/check-prompt-governance.py
python3 -m unittest tests/test_jiphyeonjeon_trust_agents.py tests/test_runtime_manifests.py tests/test_prompt_governance.py tests/test_github_actions_ec2_deploy.py
cd skills/discord-openclaw-bridge/project
uv run --with pytest pytest -q tests/test_bot.py tests/test_traveler_scout.py tests/test_traveler_evidence_deep_research.py tests/test_traveler_source_discovery.py tests/test_post_traveler_collection_report.py
```
