#!/usr/bin/env bash
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
LOG_FILE="$LOG_DIR/run_${TS}.log"

{
  echo "== run $TS =="
  "$PROJECT_DIR/.venv/bin/paper-recommender" --config "$PROJECT_DIR/config.yaml" run "$@"
} >"$LOG_FILE" 2>&1

tail -n 5 "$LOG_FILE"
echo "log: $LOG_FILE"
