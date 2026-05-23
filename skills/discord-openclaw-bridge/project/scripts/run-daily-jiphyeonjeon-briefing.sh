#!/usr/bin/env bash
# Generate the daily Jiphyeonjeon briefing artifact and post it to Discord.
#
# Kept as a committed runner so cron survives workspace cleanup/deploy cycles.
# This runner intentionally uses the daily newsletter archive briefing, not the
# weekly research-trends report, so the Discord "daily briefing" changes with
# the KST newsletter archive date.
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export TZ="${TZ:-Asia/Seoul}"

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
BRIDGE_PROJECT="$WORKSPACE/skills/discord-openclaw-bridge/project"
PAPER_SKILL="$WORKSPACE/skills/paper-recommender"
LOG_DIR="$WORKSPACE/logs"
LOG_FILE="$LOG_DIR/daily-jiphyeonjeon-briefing.log"
LOCK_DIR="${DAILY_BRIEFING_LOCK_DIR:-$WORKSPACE/.locks/daily-jiphyeonjeon-briefing.lock}"

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_DIR")"
exec >>"$LOG_FILE" 2>&1

printf "\n[%s] daily jiphyeonjeon briefing start\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"

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
if [[ -f "$BRIDGE_PROJECT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$BRIDGE_PROJECT/.env"
  set +a
fi

RUN_DATE="${NEWSLETTER_DATE:-$(date +%F)}"

if [[ ! -x "$PAPER_SKILL/scripts/newsletter-archive-briefing.sh" ]]; then
  echo "ERROR: missing newsletter archive builder: $PAPER_SKILL/scripts/newsletter-archive-briefing.sh" >&2
  exit 2
fi
if [[ ! -x "$BRIDGE_PROJECT/.venv/bin/discord-openclaw-post-briefing" ]]; then
  echo "ERROR: missing Discord bridge briefing publisher venv entrypoint" >&2
  exit 2
fi

export NEWSLETTER_DATE="$RUN_DATE"
export NEWSLETTER_WIKI_ROOT="${NEWSLETTER_WIKI_ROOT:-$WORKSPACE/wiki}"
export NEWSLETTER_REPORT_PATH="${NEWSLETTER_REPORT_PATH:-$WORKSPACE/reports/newsletter-briefing-latest.md}"
export NEWSLETTER_ARCHIVE_SOURCE="${NEWSLETTER_ARCHIVE_SOURCE:-$NEWSLETTER_WIKI_ROOT/raw/newsletters/$RUN_DATE/items.json}"
export DISCORD_BRIEFING_SOURCE="${DISCORD_BRIEFING_SOURCE:-$WORKSPACE/reports/daily-trends-latest.md}"
WAIT_SECONDS="${DAILY_BRIEFING_WAIT_SECONDS:-600}"
WAIT_INTERVAL="${DAILY_BRIEFING_WAIT_INTERVAL_SECONDS:-5}"
NEWSLETTER_LOCK_DIR="${NEWSLETTER_ARCHIVE_LOCK_DIR:-$WORKSPACE/.locks/newsletter-archive-and-cardnews.lock}"

if [[ "${DAILY_BRIEFING_DRY_RUN:-0}" == "1" ]]; then
  echo "dry-run: would use daily newsletter briefing for NEWSLETTER_DATE=$NEWSLETTER_DATE"
  echo "dry-run: NEWSLETTER_ARCHIVE_SOURCE=$NEWSLETTER_ARCHIVE_SOURCE"
  echo "dry-run: NEWSLETTER_REPORT_PATH=$NEWSLETTER_REPORT_PATH"
  echo "dry-run: would post Discord daily Jiphyeonjeon briefing from $DISCORD_BRIEFING_SOURCE"
  printf "[%s] daily jiphyeonjeon briefing dry-run complete\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"
  exit 0
fi

report_matches_run_date() {
  [[ -s "$NEWSLETTER_REPORT_PATH" ]] && grep -Fqx -- "작성일: \`$NEWSLETTER_DATE\`" "$NEWSLETTER_REPORT_PATH"
}

archive_and_report_ready() {
  [[ -s "$NEWSLETTER_ARCHIVE_SOURCE" ]] && report_matches_run_date
}

wait_for_archive_runner() {
  local elapsed=0
  while [[ -d "$NEWSLETTER_LOCK_DIR" ]]; do
    if (( elapsed >= WAIT_SECONDS )); then
      return 1
    fi
    sleep "$WAIT_INTERVAL"
    elapsed=$((elapsed + WAIT_INTERVAL))
  done
  return 0
}

elapsed=0
while ! archive_and_report_ready; do
  if (( elapsed >= WAIT_SECONDS )); then
    break
  fi
  sleep "$WAIT_INTERVAL"
  elapsed=$((elapsed + WAIT_INTERVAL))
done

if archive_and_report_ready; then
  echo "daily briefing reusing fresh newsletter archive: $NEWSLETTER_ARCHIVE_SOURCE"
else
  if ! wait_for_archive_runner; then
    echo "ERROR: newsletter archive runner lock did not clear: $NEWSLETTER_LOCK_DIR" >&2
    exit 2
  fi
  if archive_and_report_ready; then
    echo "daily briefing reusing newsletter archive after lock cleared: $NEWSLETTER_ARCHIVE_SOURCE"
  else
    if ! mkdir "$NEWSLETTER_LOCK_DIR" 2>/dev/null; then
      echo "ERROR: newsletter archive runner lock appeared before fallback build: $NEWSLETTER_LOCK_DIR" >&2
      exit 2
    fi
    trap 'rmdir "$NEWSLETTER_LOCK_DIR" 2>/dev/null || true; rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
    echo "daily briefing building newsletter archive for NEWSLETTER_DATE=$NEWSLETTER_DATE"
    "$PAPER_SKILL/scripts/newsletter-archive-briefing.sh"
    rmdir "$NEWSLETTER_LOCK_DIR" 2>/dev/null || true
    trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  fi
fi

if [[ ! -s "$NEWSLETTER_REPORT_PATH" ]]; then
  echo "ERROR: daily newsletter briefing source was not created: $NEWSLETTER_REPORT_PATH" >&2
  exit 2
fi
if ! report_matches_run_date; then
  echo "ERROR: daily newsletter briefing source does not match NEWSLETTER_DATE=$NEWSLETTER_DATE: $NEWSLETTER_REPORT_PATH" >&2
  exit 2
fi

mkdir -p "$(dirname "$DISCORD_BRIEFING_SOURCE")"
cp "$NEWSLETTER_REPORT_PATH" "$DISCORD_BRIEFING_SOURCE"
echo "daily briefing source refreshed from newsletter briefing: $DISCORD_BRIEFING_SOURCE"

cd "$BRIDGE_PROJECT"
.venv/bin/discord-openclaw-post-briefing

printf "[%s] daily jiphyeonjeon briefing done\n" "$(date +%Y-%m-%dT%H:%M:%S%z)"
