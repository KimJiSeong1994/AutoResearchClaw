#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"

ssh -i "$KEY_FILE" "$REMOTE_HOST" "REMOTE_PROJECT=$REMOTE_PROJECT bash -s" <<'REMOTE'
set -euo pipefail
PROJECT_DIR="${REMOTE_PROJECT/#\~/$HOME}"
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

"$PROJECT_DIR/.venv/bin/paper-recommender" --config "$PROJECT_DIR/config.yaml" doctor
REMOTE
