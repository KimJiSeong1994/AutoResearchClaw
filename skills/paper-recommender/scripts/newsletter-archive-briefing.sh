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
NEWSLETTER_WIKI_ROOT="${NEWSLETTER_WIKI_ROOT:-$HOME/.openclaw/workspace/wiki}"
NEWSLETTER_REPORT_PATH="${NEWSLETTER_REPORT_PATH:-$HOME/.openclaw/workspace/reports/newsletter-briefing-latest.md}"
NEWSLETTER_SENDER_ALLOWLIST="${NEWSLETTER_SENDER_ALLOWLIST:-}"
NEWSLETTER_MAX_MESSAGES="${NEWSLETTER_MAX_MESSAGES:-500}"
NEWSLETTER_MAX_SOURCE_BYTES="${NEWSLETTER_MAX_SOURCE_BYTES:-52428800}"
NEWSLETTER_DATE="${NEWSLETTER_DATE:-$(date +%F)}"

mkdir -p "$(dirname "$NEWSLETTER_REPORT_PATH")"

python3 "$SKILL_DIR/newsletter_ingest.py" \
  --source "$NEWSLETTER_EXPORT_PATH" \
  --wiki-root "$NEWSLETTER_WIKI_ROOT" \
  --date "$NEWSLETTER_DATE" \
  --sender-allowlist "$NEWSLETTER_SENDER_ALLOWLIST" \
  --max-messages "$NEWSLETTER_MAX_MESSAGES" \
  --max-source-bytes "$NEWSLETTER_MAX_SOURCE_BYTES" \
  --briefing-path "$NEWSLETTER_REPORT_PATH"

echo "newsletter briefing: $NEWSLETTER_REPORT_PATH"
