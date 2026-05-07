#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -f "$HOME/.openclaw/workspace/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$HOME/.openclaw/workspace/.env"
  set +a
fi

NEWSLETTER_EXPORT_PATH="${NEWSLETTER_EXPORT_PATH:-$HOME/.openclaw/workspace/newsletters/gmail-export.mbox}"
NEWSLETTER_SOURCE_MODE="${NEWSLETTER_SOURCE_MODE:-export}"
NEWSLETTER_WIKI_ROOT="${NEWSLETTER_WIKI_ROOT:-$HOME/.openclaw/workspace/wiki}"
NEWSLETTER_REPORT_PATH="${NEWSLETTER_REPORT_PATH:-$HOME/.openclaw/workspace/reports/newsletter-briefing-latest.md}"
NEWSLETTER_SENDER_ALLOWLIST="${NEWSLETTER_SENDER_ALLOWLIST:-}"
NEWSLETTER_MAX_MESSAGES="${NEWSLETTER_MAX_MESSAGES:-500}"
NEWSLETTER_MAX_SOURCE_BYTES="${NEWSLETTER_MAX_SOURCE_BYTES:-52428800}"
NEWSLETTER_DATE="${NEWSLETTER_DATE:-$(TZ=Asia/Seoul date +%F)}"
JIPHYEONJEON_MINER_INTAKE_PATH="${JIPHYEONJEON_MINER_INTAKE_PATH:-$HOME/.openclaw/workspace/intake/jiphyeonjeon-miner/links.jsonl}"
JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH="${JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH:-$HOME/.openclaw/workspace/review/jiphyeonjeon-claw/link-review-queue.jsonl}"
JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH="${JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH:-$HOME/.openclaw/workspace/manual_links/approved-manual-links.jsonl}"

mkdir -p "$(dirname "$NEWSLETTER_REPORT_PATH")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -x "$HOME/.openclaw/workspace/projects/paper-recommender/.venv/bin/python" ]]; then
  PYTHON_BIN="$HOME/.openclaw/workspace/projects/paper-recommender/.venv/bin/python"
fi

if [[ "$NEWSLETTER_SOURCE_MODE" == "apps_script_pull" ]]; then
  : "${APPS_SCRIPT_BRIEFING_URL:?missing APPS_SCRIPT_BRIEFING_URL}"
  : "${APPS_SCRIPT_RELAY_TOKEN:?missing APPS_SCRIPT_RELAY_TOKEN}"
  TMP_JSON="$(mktemp)"
  trap 'rm -f "$TMP_JSON"' EXIT
  URL="$APPS_SCRIPT_BRIEFING_URL"
  sep='?'
  if [[ "$URL" == *\?* ]]; then sep='&'; fi
  curl -fsSL "${URL}${sep}token=${APPS_SCRIPT_RELAY_TOKEN}&refresh=true&include_items=true" -o "$TMP_JSON"
  args=(
    "$PYTHON_BIN" "$SKILL_DIR/apps_script_relay_ingest.py"
    --payload "$TMP_JSON"
    --wiki-root "$NEWSLETTER_WIKI_ROOT"
    --date "$NEWSLETTER_DATE"
    --briefing-path "$NEWSLETTER_REPORT_PATH"
  )
  for miner_exclusion_path in \
    "$JIPHYEONJEON_MINER_INTAKE_PATH" \
    "$JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH" \
    "$JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH"
  do
    if [[ -f "$miner_exclusion_path" ]]; then
      args+=(--miner-exclusion-path "$miner_exclusion_path")
    fi
  done
  if [[ -f "$JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH" ]]; then
    args+=(--manual-links-path "$JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH")
  fi
  "${args[@]}"
elif [[ "$NEWSLETTER_SOURCE_MODE" == "gmail_api" ]]; then
  "$PYTHON_BIN" "$SKILL_DIR/gmail_newsletter_briefing.py" \
    --wiki-root "$NEWSLETTER_WIKI_ROOT" \
    --date "$NEWSLETTER_DATE" \
    --sender-allowlist "$NEWSLETTER_SENDER_ALLOWLIST" \
    --max-messages "$NEWSLETTER_MAX_MESSAGES" \
    --briefing-path "$NEWSLETTER_REPORT_PATH"
else
  "$PYTHON_BIN" "$SKILL_DIR/newsletter_ingest.py" \
    --source "$NEWSLETTER_EXPORT_PATH" \
    --wiki-root "$NEWSLETTER_WIKI_ROOT" \
    --date "$NEWSLETTER_DATE" \
    --sender-allowlist "$NEWSLETTER_SENDER_ALLOWLIST" \
    --max-messages "$NEWSLETTER_MAX_MESSAGES" \
    --max-source-bytes "$NEWSLETTER_MAX_SOURCE_BYTES" \
    --briefing-path "$NEWSLETTER_REPORT_PATH"
fi

echo "newsletter briefing: $NEWSLETTER_REPORT_PATH"
