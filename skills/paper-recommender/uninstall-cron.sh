#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"

ssh -i "$KEY_FILE" "$REMOTE_HOST" bash -s <<'REMOTE'
set -euo pipefail
TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "paper-recommender-daily" | grep -v "run_daily.sh" > "$TMP" || true
crontab "$TMP"
rm -f "$TMP"
echo "removed paper-recommender cron entry (if any)"
REMOTE
