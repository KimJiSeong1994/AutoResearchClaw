#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_SKILL='~/.openclaw/workspace/skills/discord-openclaw-bridge'
SSH_BASE=(ssh)
if [[ -n "${SSH_OPTIONS:-}" ]]; then
  # shellcheck disable=SC2206
  SSH_EXTRA_OPTIONS=(${SSH_OPTIONS})
  SSH_BASE+=("${SSH_EXTRA_OPTIONS[@]}")
fi
SSH_BASE+=(-i "$KEY_FILE")
RSYNC_SSH="${SSH_BASE[*]}"

cd "$ROOT_DIR"
"${SSH_BASE[@]}" "$REMOTE_HOST" "mkdir -p $REMOTE_SKILL"
COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  skills/discord-openclaw-bridge/ \
  "$REMOTE_HOST:$REMOTE_SKILL/"
"${SSH_BASE[@]}" "$REMOTE_HOST" "find $REMOTE_SKILL -name '._*' -delete; find $REMOTE_SKILL/project/scripts -name '*.sh' -exec chmod +x {} +"

echo "Deployed Discord OpenClaw bridge to $REMOTE_HOST:$REMOTE_SKILL"
echo "Remote install: ssh ... 'cd $REMOTE_SKILL && bash project/scripts/install.sh'"
