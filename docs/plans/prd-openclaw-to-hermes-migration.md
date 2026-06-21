# PRD: OpenClaw to Hermes Agent migration

## Goal

Migrate the AutoResearchClaw operating surface from an OpenClaw-centered runtime to Hermes Agent without losing the existing Discord, ResearchClaw, paper recommendation, review queue, and publication safety boundaries.

## Non-goals

- No production service restart in the planning/adapter phase.
- No secret migration or secret value printing.
- No destructive deletion of OpenClaw state, auth files, session state, logs, queues, or review artifacts.
- No broad CLI/service rename until compatibility tests and rollback paths exist.

## Current constraints

- Runtime manifests still default to `openclaw-ec2`, `~/.openclaw/workspace`, `http://127.0.0.1:18789/v1`, and `openclaw/clawbridge`.
- Deployment and readiness scripts directly call OpenClaw paths, services, and CLI commands.
- Discord bridge and paper recommender use OpenAI-compatible `/chat/completions` gateway semantics through OpenClaw-named modules.
- Runtime manifest and prompt governance tests encode OpenClaw IDs and service surfaces as validation invariants.

## Migration strategy

### Phase 1: Compatibility adapter first

Introduce Hermes-compatible configuration aliases while preserving the current OpenClaw names and defaults. This lets staging use Hermes by setting Hermes env vars without breaking production OpenClaw deployments.

Priority aliases:

- `HERMES_BASE_URL` before `OPENCLAW_BASE_URL`
- `HERMES_GATEWAY_TOKEN` before `OPENCLAW_GATEWAY_TOKEN`
- `HERMES_GATEWAY_TOKEN_FILE` before `OPENCLAW_GATEWAY_TOKEN_FILE`
- `HERMES_MODEL` before `OPENCLAW_MODEL`
- `HERMES_TIMEOUT_SEC` before `OPENCLAW_TIMEOUT_SEC`

Loopback-only policy remains mandatory for Discord bridge traffic.

### Phase 2: Runtime manifest dual naming

Add Hermes fields or companion manifests while preserving OpenClaw job IDs until downstream scripts and tests are migrated. The compatibility period should keep existing `discord-openclaw-*` commands operational.

### Phase 3: Ops/deploy split

Add Hermes-specific deploy/readiness scripts rather than editing the OpenClaw scripts in place. This enables side-by-side canary checks and clear rollback.

### Phase 4: Service canary

Run Hermes in a staging loopback endpoint, then canary only read-only and dry-run paths before production side effects.

Recommended canary order:

1. gateway `/models`/chat smoke
2. Discord bridge health and dry-run command path
3. ResearchClaw validate-only
4. paper recommender dry run
5. publication/reporting dry runs

### Phase 5: Rename and cleanup

Only after canary success, migrate user-facing names and validation invariants from OpenClaw to Hermes. Keep rollback aliases for at least one deploy cycle.

## Acceptance criteria

- Hermes env aliases are accepted without changing existing OpenClaw env behavior.
- OpenClaw defaults remain unchanged when Hermes env vars are absent.
- Loopback URL enforcement still rejects non-loopback Hermes and OpenClaw base URLs.
- Migration planning docs and test spec exist under `docs/plans/`.
- Local targeted tests pass for modified gateway/config behavior.

## Rollback

- Unset Hermes env vars to return bridge behavior to OpenClaw env/defaults.
- Revert adapter changes and rerun prompt/runtime validation before redeploy.
- Do not delete Hermes or OpenClaw state as part of rollback unless separately approved.
