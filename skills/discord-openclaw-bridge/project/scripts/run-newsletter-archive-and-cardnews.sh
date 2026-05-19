#!/usr/bin/env bash
# Build the daily newsletter archive and publish archive + card-news to Discord.
#
# This file is intentionally committed and rsynced by the cron installer.  The
# crontab must never point at a heredoc-generated runner that can disappear
# during workspace cleanup/deploy.
set -euo pipefail

export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export TZ="${TZ:-Asia/Seoul}"

WORKSPACE="${OPENCLAW_WORKSPACE:-$HOME/.openclaw/workspace}"
BRIDGE_PROJECT="$WORKSPACE/skills/discord-openclaw-bridge/project"
PAPER_SKILL="$WORKSPACE/skills/paper-recommender"
LOG_DIR="$WORKSPACE/logs"
RUN_DATE="${NEWSLETTER_DATE:-$(date +%F)}"
LOG_FILE="$LOG_DIR/newsletter-archive-and-cardnews.log"
LOCK_DIR="${NEWSLETTER_ARCHIVE_LOCK_DIR:-$WORKSPACE/.locks/newsletter-archive-and-cardnews.lock}"

mkdir -p "$LOG_DIR" "$(dirname "$LOCK_DIR")"
exec >>"$LOG_FILE" 2>&1

printf "\n[%s] newsletter archive + card-news publish start\n" "$(date -Is)"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "another newsletter archive/card-news run is already active: $LOCK_DIR"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

if [[ -f "$WORKSPACE/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$WORKSPACE/.env"
  set +a
fi

if [[ ! -x "$PAPER_SKILL/scripts/newsletter-archive-briefing.sh" ]]; then
  echo "ERROR: missing newsletter archive builder: $PAPER_SKILL/scripts/newsletter-archive-briefing.sh" >&2
  exit 2
fi
if [[ ! -x "$BRIDGE_PROJECT/.venv/bin/discord-openclaw-post-newsletter-archive" ]]; then
  echo "ERROR: missing Discord bridge archive publisher venv entrypoint" >&2
  exit 2
fi
if [[ ! -x "$BRIDGE_PROJECT/.venv/bin/discord-openclaw-post-card-news" ]]; then
  echo "ERROR: missing Discord bridge card-news publisher venv entrypoint" >&2
  exit 2
fi

export NEWSLETTER_DATE="$RUN_DATE"
export NEWSLETTER_WIKI_ROOT="${NEWSLETTER_WIKI_ROOT:-$WORKSPACE/wiki}"
export NEWSLETTER_REPORT_PATH="${NEWSLETTER_REPORT_PATH:-$WORKSPACE/reports/newsletter-briefing-latest.md}"
export NEWSLETTER_ARCHIVE_SOURCE="${NEWSLETTER_ARCHIVE_SOURCE:-$NEWSLETTER_WIKI_ROOT/raw/newsletters/$RUN_DATE/items.json}"
export DISCORD_CARD_NEWS_SOURCE="${DISCORD_CARD_NEWS_SOURCE:-$NEWSLETTER_ARCHIVE_SOURCE}"
export DISCORD_NEWSLETTER_ARCHIVE_CHANNEL_ID="${DISCORD_NEWSLETTER_ARCHIVE_CHANNEL_ID:-1501073491921993758}"
export DISCORD_CARD_NEWS_CHANNEL_ID="${DISCORD_CARD_NEWS_CHANNEL_ID:-1501211608104566854}"

if [[ "${NEWSLETTER_ARCHIVE_DRY_RUN:-0}" == "1" ]]; then
  echo "dry-run: would build newsletter archive for NEWSLETTER_DATE=$NEWSLETTER_DATE"
  echo "dry-run: NEWSLETTER_ARCHIVE_SOURCE=$NEWSLETTER_ARCHIVE_SOURCE"
  echo "dry-run: would post newsletter archive and card-news via Discord bridge"
  printf "[%s] newsletter archive + card-news dry-run complete\n" "$(date -Is)"
  exit 0
fi

"$PAPER_SKILL/scripts/newsletter-archive-briefing.sh"

if [[ ! -s "$NEWSLETTER_ARCHIVE_SOURCE" ]]; then
  echo "ERROR: newsletter archive source was not created: $NEWSLETTER_ARCHIVE_SOURCE" >&2
  exit 2
fi

cd "$BRIDGE_PROJECT"
.venv/bin/discord-openclaw-post-newsletter-archive
.venv/bin/discord-openclaw-post-card-news

printf "[%s] newsletter archive + card-news publish done\n" "$(date -Is)"
