#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"

ssh -i "$KEY_FILE" "$REMOTE_HOST" bash -s <<'REMOTE'
set -euo pipefail
TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "paper-recommender-daily" | grep -v "run_daily.sh" > "$TMP" || true
crontab "$TMP"
rm -f "$TMP"
echo "removed paper-recommender cron entry (if any)"
REMOTE
