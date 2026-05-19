#!/usr/bin/env bash
# Generate the daily/weekly Jiphyeonjeon briefing artifact and post it to Discord.
#
# Kept as a committed runner so cron survives workspace cleanup/deploy cycles.
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export TZ="${TZ:-Asia/Seoul}"

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
BRIDGE_PROJECT="$WORKSPACE/skills/discord-openclaw-bridge/project"
PAPER_PROJECT="$WORKSPACE/projects/paper-recommender"
LOG_DIR="$WORKSPACE/logs"
LOG_FILE="$LOG_DIR/daily-jiphyeonjeon-briefing.log"
LOCK_DIR="${DAILY_BRIEFING_LOCK_DIR:-$WORKSPACE/.locks/daily-jiphyeonjeon-briefing.lock}"

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_DIR")"
exec >>"$LOG_FILE" 2>&1

printf "\n[%s] daily jiphyeonjeon briefing start\n" "$(date -Is)"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another daily jiphyeonjeon briefing run is already active: $LOCK_DIR"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ -f "$WORKSPACE/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$WORKSPACE/.env"
  set +a
fi

if [[ ! -x "$PAPER_PROJECT/.venv/bin/python" ]]; then
  echo "ERROR: missing paper-recommender venv python" >&2
  exit 2
fi
if [[ ! -x "$BRIDGE_PROJECT/.venv/bin/discord-openclaw-post-briefing" ]]; then
  echo "ERROR: missing Discord bridge briefing publisher venv entrypoint" >&2
  exit 2
fi

if [[ "${DAILY_BRIEFING_DRY_RUN:-0}" == "1" ]]; then
  echo "dry-run: would generate paper_recommender weekly-report --force"
  echo "dry-run: would post Discord daily Jiphyeonjeon briefing"
  printf "[%s] daily jiphyeonjeon briefing dry-run complete\n" "$(date -Is)"
  exit 0
fi

cd "$PAPER_PROJECT"
.venv/bin/python -m paper_recommender --config config.yaml weekly-report --force

cd "$BRIDGE_PROJECT"
.venv/bin/discord-openclaw-post-briefing

printf "[%s] daily jiphyeonjeon briefing done\n" "$(date -Is)"
