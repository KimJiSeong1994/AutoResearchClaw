#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_WORKSPACE='~/.openclaw/workspace'
SSH_BASE=(ssh)
if [[ -n "${SSH_OPTIONS:-}" ]]; then
  # shellcheck disable=SC2206
  SSH_EXTRA_OPTIONS=(${SSH_OPTIONS})
  SSH_BASE+=("${SSH_EXTRA_OPTIONS[@]}")
fi
SSH_BASE+=(-i "$KEY_FILE")
RSYNC_SSH="$(printf "%q " "${SSH_BASE[@]}")"

cd "$ROOT_DIR"

python3 scripts/check-prompt-governance.py
python3 scripts/check-runtime-manifests.py

"${SSH_BASE[@]}" "$REMOTE_HOST" "mkdir -p $REMOTE_WORKSPACE/skills $REMOTE_WORKSPACE/runtime $REMOTE_WORKSPACE/scripts"

COPYFILE_DISABLE=1 rsync -az \
  -e "$RSYNC_SSH" \
  workspace/AGENTS.md \
  workspace/IDENTITY.md \
  workspace/SOUL.md \
  workspace/TOOLS.md \
  workspace/USER.md \
  workspace/MEMORY.md \
  workspace/HEARTBEAT.md \
  workspace/PROMPT_GOVERNANCE.md \
  workspace/PROMPT_REGISTRY.json \
  "$REMOTE_HOST:$REMOTE_WORKSPACE/"

COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  skills/ \
  "$REMOTE_HOST:$REMOTE_WORKSPACE/skills/"
COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  runtime/ \
  "$REMOTE_HOST:$REMOTE_WORKSPACE/runtime/"

COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  scripts/ \
  "$REMOTE_HOST:$REMOTE_WORKSPACE/scripts/"

"${SSH_BASE[@]}" "$REMOTE_HOST" "find $REMOTE_WORKSPACE -maxdepth 2 -name '._*' -delete; find $REMOTE_WORKSPACE/skills $REMOTE_WORKSPACE/scripts -name '*.sh' -exec chmod +x {} +; find $REMOTE_WORKSPACE/scripts -name '*.py' -exec chmod +x {} +"
if ! "${SSH_BASE[@]}" "$REMOTE_HOST" "export PATH=\$HOME/.npm-global/bin:\$PATH; timeout 20s openclaw agents set-identity --workspace ~/.openclaw/workspace --from-identity >/dev/null 2>&1"; then
  echo "warning: openclaw identity refresh failed; workspace files were deployed" >&2
fi

echo "Deployed workspace files to $REMOTE_HOST:$REMOTE_WORKSPACE"
