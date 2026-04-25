#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"
# KST 08:00 => UTC 23:00 (previous day)
CRON_SCHEDULE="${CRON_SCHEDULE:-0 23 * * *}"
CRON_TAG="# paper-recommender-daily"

ssh -i "$KEY_FILE" "$REMOTE_HOST" "REMOTE_PROJECT=$REMOTE_PROJECT CRON_SCHEDULE='$CRON_SCHEDULE' CRON_TAG='$CRON_TAG' bash -s" <<'REMOTE'
set -euo pipefail
PROJECT_DIR="${REMOTE_PROJECT/#\~/$HOME}"

TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "paper-recommender-daily" | grep -v "run_daily.sh" > "$TMP" || true

cat >> "$TMP" <<EOF
$CRON_TAG
$CRON_SCHEDULE PATH=\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin bash $PROJECT_DIR/scripts/run_daily.sh >> $PROJECT_DIR/logs/cron.log 2>&1
EOF

crontab "$TMP"
rm -f "$TMP"

echo "installed crontab:"
crontab -l | tail -3
REMOTE
