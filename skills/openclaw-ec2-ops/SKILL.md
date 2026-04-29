---
name: openclaw_ec2_ops
description: Inspect, verify, and recover the OpenClaw gateway running on the EC2 host from inside its workspace.
---

# OpenClaw EC2 Ops

Use this skill when the user asks about:

- OpenClaw gateway status
- gateway health or logs
- service restarts
- workspace or skill deployment verification
- listener/auth/bind checks

## Primary commands

Run these scripts from the skill directory:

- `{baseDir}/gateway-status.sh`
- `{baseDir}/gateway-health.sh`
- `{baseDir}/service-status.sh`
- `{baseDir}/recent-log.sh`
- `{baseDir}/workspace-summary.sh`

If the user explicitly asks for a controlled restart, use:

- `{baseDir}/restart-gateway.sh`

## Safety rules

- Keep the gateway on loopback unless the human explicitly asks otherwise.
- Do not print the secret token value unless the human explicitly asks for it.
- Do not delete sessions, auth files, or agent state without explicit approval.
- Report evidence, not just conclusions.
- For non-trivial recovery, state the working assumption first, choose the smallest reversible operation, and verify before escalating.
- Do not "clean up" unrelated services, configs, or logs while fixing the requested gateway issue.

## Reporting checklist

Always summarize:

1. service state
2. listener state
3. health/probe result
4. recent log signal
5. any action taken
