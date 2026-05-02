#!/usr/bin/env bash
# Install / replace the paper-recommender cron(s) on EC2.
#
# Modes:
#   daily             — legacy daily picks (`paper-recommender run`)
#   daily-research    — new multi-source + deep-bridge pipeline
#   both              — both, staggered (daily 23:00 UTC, daily-research 22:30 UTC)
#
# Usage:
#   bash install-cron.sh                         # mode=daily (legacy default)
#   bash install-cron.sh --mode daily-research
#   bash install-cron.sh --mode both
#   CRON_SCHEDULE_DAILY="0 22 * * *" bash install-cron.sh --mode daily-research
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"

# Defaults:
# - legacy `daily` at KST 08:00 = UTC 23:00.
# - `daily-research` at KST 05:00 = UTC 20:00 — starts 3h before legacy so the
#   75-105 min serial deep run finishes by ~UTC 21:45 (KST 06:45) without
#   contending with the legacy cron's gateway worker. Lands well before
#   normal 09:00 KST Obsidian check.
CRON_SCHEDULE_DAILY="${CRON_SCHEDULE_DAILY:-0 23 * * *}"
CRON_SCHEDULE_DAILY_RESEARCH="${CRON_SCHEDULE_DAILY_RESEARCH:-0 20 * * *}"

MODE="daily"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --mode=*)
      MODE="${1#--mode=}"
      shift
      ;;
    -h|--help)
      sed -n '1,15p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

case "$MODE" in
  daily|daily-research|both) ;;
  *)
    echo "ERROR: --mode must be one of: daily, daily-research, both (got: $MODE)" >&2
    exit 2
    ;;
esac

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_PROJECT=$REMOTE_PROJECT MODE='$MODE' \
   CRON_SCHEDULE_DAILY='$CRON_SCHEDULE_DAILY' \
   CRON_SCHEDULE_DAILY_RESEARCH='$CRON_SCHEDULE_DAILY_RESEARCH' \
   bash -s" <<'REMOTE'
set -euo pipefail
PROJECT_DIR="${REMOTE_PROJECT/#\~/$HOME}"

TMP=$(mktemp)
# Strip ALL paper-recommender entries (any of the three CRON_TAGs) so this
# script is fully idempotent and a mode change cleanly replaces.
crontab -l 2>/dev/null | \
  grep -v "paper-recommender-daily" | \
  grep -v "paper-recommender-daily-research" | \
  grep -v "run_daily.sh" | \
  grep -v "run_daily_research.sh" > "$TMP" || true

if [ "$MODE" = "daily" ] || [ "$MODE" = "both" ]; then
  cat >> "$TMP" <<EOF
# paper-recommender-daily
$CRON_SCHEDULE_DAILY PATH=\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin bash $PROJECT_DIR/scripts/run_daily.sh >> $PROJECT_DIR/logs/cron.log 2>&1
EOF
fi

if [ "$MODE" = "daily-research" ] || [ "$MODE" = "both" ]; then
  cat >> "$TMP" <<EOF
# paper-recommender-daily-research
$CRON_SCHEDULE_DAILY_RESEARCH PATH=\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin bash $PROJECT_DIR/scripts/run_daily_research.sh >> $PROJECT_DIR/logs/cron.log 2>&1
EOF
fi

crontab "$TMP"
rm -f "$TMP"

echo "installed crontab (mode=$MODE):"
crontab -l | grep -E "paper-recommender|run_daily" || echo "(no paper-recommender entries)"
REMOTE
