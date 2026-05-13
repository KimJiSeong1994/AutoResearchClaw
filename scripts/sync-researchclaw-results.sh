#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_ARTIFACTS="~/.openclaw/workspace/projects/AutoResearchClaw/artifacts/"
LOCAL_ROOT="${LOCAL_ROOT:?Set LOCAL_ROOT to the local artifact sync directory}"

mkdir -p "$LOCAL_ROOT"
ssh -i "$KEY_FILE" "$REMOTE_HOST" "mkdir -p ~/.openclaw/workspace/projects/AutoResearchClaw/artifacts"

rsync -az --delete-delay \
  -e "ssh -i $KEY_FILE" \
  "${REMOTE_HOST}:${REMOTE_ARTIFACTS}" \
  "$LOCAL_ROOT/"

echo "Synced AutoResearchClaw artifacts to:"
echo "  $LOCAL_ROOT"
