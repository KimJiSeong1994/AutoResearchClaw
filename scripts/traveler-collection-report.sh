#!/usr/bin/env bash
# Stable EC2 cron entrypoint. Delegates to the committed Traveler runner.
set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
exec "$WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh" "$@"
