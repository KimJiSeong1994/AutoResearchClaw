#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
HERMES_REMOTE_WORKSPACE="${HERMES_REMOTE_WORKSPACE:-~/.hermes/workspace}"
case "$HERMES_REMOTE_WORKSPACE" in
  "~/.hermes/"*|"~/.hermes") ;;
  *) echo "FAIL: HERMES_REMOTE_WORKSPACE must stay under ~/.hermes" >&2; exit 1 ;;
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

quote_remote() { printf '%q' "$1"; }

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
REMOTE_SKILL="$HERMES_REMOTE_WORKSPACE/skills/discord-openclaw-bridge"
remote_skill_quoted="$(quote_remote "$REMOTE_SKILL")"
"${SSH_BASE[@]}" "$REMOTE_HOST" "mkdir -p $remote_skill_quoted"
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
"${SSH_BASE[@]}" "$REMOTE_HOST" "find $remote_skill_quoted -name '._*' -delete; find $remote_skill_quoted/project/scripts -name '*.sh' -exec chmod +x {} +"

echo "Deployed Discord bridge source to $REMOTE_HOST:$REMOTE_SKILL"
echo "Hermes remains the only live deployment target; ~/.openclaw is retained for rollback only."
