#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
HERMES_REMOTE_WORKSPACE="${HERMES_REMOTE_WORKSPACE:-~/.hermes/workspace}"
case "$HERMES_REMOTE_WORKSPACE" in
  "~/.hermes/"*|"~/.hermes")
    ;;
  *)
    echo "FAIL: HERMES_REMOTE_WORKSPACE must stay under the ~/.hermes canary directory" >&2
    exit 1
    ;;
esac
case "$HERMES_REMOTE_WORKSPACE" in
  *[[:space:]]*|*[\;\"\'\`\$\\\&\|\<\>\(\)\*\?\[\]]*)
    echo "FAIL: HERMES_REMOTE_WORKSPACE contains unsafe shell characters" >&2
    exit 1
    ;;
esac
case "$HERMES_REMOTE_WORKSPACE" in
  *"/../"*|*"../"*|*".."|*"/..")
    echo "FAIL: HERMES_REMOTE_WORKSPACE must not contain parent-directory traversal" >&2
    exit 1
    ;;
esac

quote_remote() {
  printf '%q' "$1"
}

SSH_BASE=(ssh)
if [[ -n "${SSH_OPTIONS:-}" ]]; then
  # shellcheck disable=SC2206
  SSH_EXTRA_OPTIONS=(${SSH_OPTIONS})
  SSH_BASE+=("${SSH_EXTRA_OPTIONS[@]}")
fi
SSH_BASE+=(-i "$KEY_FILE")
RSYNC_SSH=""
for ssh_arg in "${SSH_BASE[@]}"; do
  RSYNC_SSH+="${RSYNC_SSH:+ }$(quote_remote "$ssh_arg")"
done

cd "$ROOT_DIR"

python3 scripts/check-prompt-governance.py
python3 scripts/check-runtime-manifests.py

remote_workspace_quoted="$(quote_remote "$HERMES_REMOTE_WORKSPACE")"

"${SSH_BASE[@]}" "$REMOTE_HOST" "mkdir -p $remote_workspace_quoted/skills $remote_workspace_quoted/runtime $remote_workspace_quoted/scripts"

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
  "$REMOTE_HOST:$HERMES_REMOTE_WORKSPACE/"

COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  skills/ \
  "$REMOTE_HOST:$HERMES_REMOTE_WORKSPACE/skills/"

COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  runtime/ \
  "$REMOTE_HOST:$HERMES_REMOTE_WORKSPACE/runtime/"

COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$RSYNC_SSH" \
  scripts/ \
  "$REMOTE_HOST:$HERMES_REMOTE_WORKSPACE/scripts/"

"${SSH_BASE[@]}" "$REMOTE_HOST" "find $remote_workspace_quoted -maxdepth 2 -name '._*' -delete; find $remote_workspace_quoted/skills $remote_workspace_quoted/scripts -name '*.sh' -exec chmod +x {} +; find $remote_workspace_quoted/scripts -name '*.py' -exec chmod +x {} +"

echo "Deployed Hermes canary workspace files to $REMOTE_HOST:$HERMES_REMOTE_WORKSPACE"
