---
name: karpathy-guidelines
description: Apply Karpathy-inspired agent discipline when writing, reviewing, refactoring, or operating this OpenClaw workspace: surface assumptions, keep changes simple, edit surgically, and verify explicit success criteria.
---

# Karpathy Guidelines for AutoResearchClaw

Use this skill before non-trivial code, prompt, skill, workflow, or operational changes in this workspace.

The point is not to slow down obvious one-line fixes. The point is to prevent common agent mistakes: hidden assumptions, over-built abstractions, drive-by refactors, and claims of completion without evidence.

## 1. Think before changing

- State the working assumption if the request has more than one plausible interpretation.
- Name uncertainty instead of hiding it.
- Surface tradeoffs when a simpler path exists or when the requested path is risky.
- Ask only when the ambiguity would materially change the implementation, affect secrets, or require irreversible action.

## 2. Simplicity first

- Implement the smallest clear solution that satisfies the current request.
- Do not add speculative features, switches, config layers, caches, retries, plugin points, or abstractions.
- Do not introduce new dependencies unless explicitly requested or clearly required.
- If the solution becomes large, pause and simplify before continuing.

## 3. Surgical changes

- Touch only files and lines that trace directly to the user's goal.
- Match the existing style even when you would normally prefer another style.
- Do not reformat, rename, or refactor adjacent code while making an unrelated fix.
- Remove imports, variables, functions, or docs made unused by your own change.
- Mention unrelated dead code or design issues in the final report; do not delete them unless asked.

## 4. Goal-driven execution

Turn imperative requests into verifiable targets:

1. Define success criteria.
2. Prefer a failing reproduction or narrow baseline check before a bug fix/refactor.
3. Make the smallest change that should satisfy the criteria.
4. Run the narrowest relevant verification.
5. Broaden verification only when the change surface warrants it.
6. Report the evidence and any remaining risk.

## AutoResearchClaw-specific checklist

- **Gateway work:** prove service state, listener state, health probe, and recent logs.
- **ResearchClaw work:** validate local/remote config, gateway endpoint, auth env, and a targeted pipeline command.
- **Paper recommender work:** run targeted tests or `doctor.sh` before a real recommendation run; keep cron and Obsidian sync concerns separate unless the user asked to join them.
- **Workspace prompt/skill work:** keep instructions concise, non-duplicative, and compatible with OpenClaw's loopback/security defaults.

## Final response shape

For non-trivial work, include:

- assumptions or interpretation chosen
- files changed
- verification evidence
- remaining risks or intentionally deferred adjacent cleanup
