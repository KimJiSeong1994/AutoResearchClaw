#!/usr/bin/env bash
# Stable EC2 cron entrypoint. Delegates to the committed Miner seeds runner.
set -euo pipefail

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
exec "$WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-miner-seeds.sh" "$@"
