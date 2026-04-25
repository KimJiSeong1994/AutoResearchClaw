#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="/Users/jiseong/git/PaperReviewAgent/jiseong.pem"
REMOTE_HOST="ubuntu@52.79.96.56"
REMOTE_ARTIFACTS="~/.openclaw/workspace/projects/AutoResearchClaw/artifacts/"
LOCAL_ROOT="/Users/jiseong/Library/Mobile Documents/iCloud~md~obsidian/Documents/Write Paper/AutoResearchClaw"

mkdir -p "$LOCAL_ROOT"
ssh -i "$KEY_FILE" "$REMOTE_HOST" "mkdir -p ~/.openclaw/workspace/projects/AutoResearchClaw/artifacts"

rsync -az --delete-delay \
  -e "ssh -i $KEY_FILE" \
  "${REMOTE_HOST}:${REMOTE_ARTIFACTS}" \
  "$LOCAL_ROOT/"

echo "Synced AutoResearchClaw artifacts to:"
echo "  $LOCAL_ROOT"
