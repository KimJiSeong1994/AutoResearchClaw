#!/usr/bin/env bash
# Stable EC2 cron entrypoint.  Delegates to the committed skill runner.
set -euo pipefail

WORKSPACE="${HERMES_WORKSPACE:-${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}}"
exec "$WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-newsletter-archive-and-cardnews.sh" "$@"
