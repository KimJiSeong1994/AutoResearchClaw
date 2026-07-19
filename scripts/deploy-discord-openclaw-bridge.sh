#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
# Both workspaces are live and neither is a symlink to the other: the
# discord-openclaw-bridge service runs from ~/.openclaw, while the traveler,
# miner, and briefing crons plus the discord-hermes-* services run from
# ~/.hermes. Deploying only to ~/.openclaw left the cron executing months-old
# code with no sign anything was wrong, so deploy to every live workspace.
REMOTE_WORKSPACES="${REMOTE_WORKSPACES:-~/.openclaw/workspace ~/.hermes/workspace}"
SSH_BASE=(ssh)
if [[ -n "${SSH_OPTIONS:-}" ]]; then
  # shellcheck disable=SC2206
  SSH_EXTRA_OPTIONS=(${SSH_OPTIONS})
  SSH_BASE+=("${SSH_EXTRA_OPTIONS[@]}")
fi
SSH_BASE+=(-i "$KEY_FILE")
RSYNC_SSH="${SSH_BASE[*]}"

cd "$ROOT_DIR"
for REMOTE_WORKSPACE in $REMOTE_WORKSPACES; do
  REMOTE_SKILL="$REMOTE_WORKSPACE/skills/discord-openclaw-bridge"
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
done

# The package is installed editable in each workspace venv, so synced source is
# live without a reinstall. Rerunning install.sh here would be wrong: it does
# `uv venv --clear` and rewrites the shared systemd unit.
echo "Remote install (only when dependencies or entry points changed): ssh ... 'cd <workspace>/skills/discord-openclaw-bridge && bash project/scripts/install.sh'"
