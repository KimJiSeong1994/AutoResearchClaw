#!/usr/bin/env bash
# Daily-research cron entrypoint. Mirrors run_daily.sh but invokes the new
# `daily-research` subcommand (multi-source + clustering + deep bridge).
#
# Loads the same env files as run_daily.sh so JIPHYEONJEON_USERNAME /
# JIPHYEONJEON_PASSWORD (login-based auth) and OPENCLAW_GATEWAY_TOKEN are
# in scope before paper-recommender starts.
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/.openclaw/workspace/projects/paper-recommender}"
cd "$PROJECT_DIR"

if [ -f "$HOME/.openclaw_gateway_token" ]; then
  export OPENCLAW_GATEWAY_TOKEN="$(tr -d '\n' < "$HOME/.openclaw_gateway_token")"
fi

if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_DIR/.env"
  set +a
fi

LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/daily_research_${TS}.log"

# Rotate: drop daily-research logs older than 14 days. Without this they
# accumulate at ~30 MB/run × 365 runs/yr = ~11 GB/yr on the EC2 EBS.
find "$LOG_DIR" -maxdepth 1 -name "daily_research_*.log" -mtime +14 -delete \
  2>/dev/null || true

{
  echo "== daily-research $TS =="
  "$PROJECT_DIR/.venv/bin/paper-recommender" \
    --config "$PROJECT_DIR/config.yaml" daily-research "$@"
} >"$LOG_FILE" 2>&1

tail -n 5 "$LOG_FILE"
echo "log: $LOG_FILE"
