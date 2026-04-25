#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_PROJECT="$SKILL_DIR/project"

ssh -i "$KEY_FILE" "$REMOTE_HOST" "mkdir -p $REMOTE_PROJECT"

rsync -az --delete \
  --exclude ".venv" \
  --exclude "artifacts" \
  --exclude "logs" \
  --exclude "state" \
  --exclude "__pycache__" \
  --exclude "*.egg-info" \
  --exclude ".omc" \
  --exclude ".env" \
  -e "ssh -i $KEY_FILE" \
  "$LOCAL_PROJECT/" \
  "${REMOTE_HOST}:${REMOTE_PROJECT}/"

echo "deployed project to ${REMOTE_HOST}:${REMOTE_PROJECT}"
