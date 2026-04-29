# AGENTS.md - OpenClaw EC2 Control Workspace

This workspace is the control plane for the OpenClaw gateway running on the EC2 host.

## Mission

- Keep the OpenClaw gateway healthy and reachable.
- Prefer safe, loopback-only operation.
- Treat this repo as the canonical source for the remote workspace files.

## Operating rules

- Use the `openclaw_ec2_ops` skill for gateway status, logs, restart, and workspace inspection.
- Use the `researchclaw` skill for AutoResearchClaw setup, validation, and pipeline execution.
- Use the `karpathy-guidelines` skill before non-trivial coding, refactoring, review, or workflow-design changes.
- Prefer local OpenClaw CLI commands on the host over ad-hoc process poking.
- Keep the gateway bound to `127.0.0.1` unless the human explicitly asks to expose it differently.
- Do not print the gateway token unless the human explicitly asks for the secret value.
- Do not change auth mode, bind mode, or delete session state without explicit approval.

## Karpathy-inspired execution discipline

This workspace adopts the `forrestchang/andrej-karpathy-skills` philosophy for reducing LLM coding mistakes:

1. **Think before changing:** state assumptions, name ambiguity, and surface tradeoffs before implementation.
2. **Simplicity first:** solve today's problem with the smallest clear change; do not add speculative abstractions, features, or configurability.
3. **Surgical edits:** touch only files and lines that trace directly to the user request; match existing style; mention unrelated dead code instead of deleting it.
4. **Goal-driven verification:** convert work into explicit success criteria, prefer tests or probes that reproduce the issue first, and loop until the evidence proves completion.

For trivial one-line fixes, use judgment and stay lightweight. For non-trivial work, every changed line should be explainable by a success criterion.

## Response style

- Lead with current gateway state.
- Include concrete evidence: process state, listener, service state, recent logs.
- If you changed anything, say what changed and how to verify it.

## Memory

- Record durable operational lessons in `MEMORY.md`.
- Keep environment-specific facts in `TOOLS.md`.
- Keep user-specific preferences in `USER.md`.
