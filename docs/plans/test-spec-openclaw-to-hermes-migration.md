# Test spec: OpenClaw to Hermes Agent migration

## Scope

This spec covers the first safe implementation slice: configuration-level Hermes compatibility for existing OpenAI-compatible gateway calls.

## Unit tests

### Discord bridge config

- `HERMES_BASE_URL` is used before `OPENCLAW_BASE_URL`.
- `HERMES_GATEWAY_TOKEN` is used before `OPENCLAW_GATEWAY_TOKEN`.
- `HERMES_GATEWAY_TOKEN_FILE` is used before `OPENCLAW_GATEWAY_TOKEN_FILE` when direct tokens are absent.
- `HERMES_MODEL` and `HERMES_TIMEOUT_SEC` are used before OpenClaw equivalents.
- Existing OpenClaw env vars still work when Hermes env vars are absent.
- Non-loopback Hermes base URLs are rejected.

### Gateway payload compatibility

- `/chat/completions` payload shape remains unchanged for current OpenAI-compatible gateway users.
- Authorization header is preserved.
- `/models` health path remains available while the compatibility gateway uses `/v1` style base URLs.

## Integration checks

Run before implementation claim:

```bash
python3 scripts/check-prompt-governance.py
python3 scripts/check-runtime-manifests.py
cd skills/discord-openclaw-bridge/project && uv run pytest tests/test_openclaw_gateway.py tests/test_openclaw_client.py tests/test_bot.py -q
```

Run broader checks before production deploy:

```bash
python3 -m unittest tests/test_prompt_governance.py tests/test_runtime_manifests.py tests/test_github_actions_ec2_deploy.py
cd skills/discord-openclaw-bridge/project && uv run pytest -q
```

## Production gate

No production deployment until:

1. Hermes staging endpoint has a loopback-only health probe.
2. Discord bridge dry-run/smoke passes with Hermes env aliases.
3. OpenClaw fallback path is verified by unsetting Hermes env vars.
4. Rollback instructions are present in the deployment note.
