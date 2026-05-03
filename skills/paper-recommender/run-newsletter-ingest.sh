#!/usr/bin/env bash
set -euo pipefail

# Publish local Google/Gmail newsletter exports into the local PaperWiki vault.
# This script intentionally does not authenticate to Google. Drop a Gmail
# Takeout .mbox or sanitized .jsonl export into NEWSLETTER_SOURCE_DIR.

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIKI_ROOT="${WIKI_ROOT:-/Users/jiseong/Library/Mobile Documents/com~apple~CloudDocs/PaperWiki/PaperWiki}"
FALLBACK_WIKI_ROOT="${FALLBACK_WIKI_ROOT:-$HOME/Desktop/paper-wiki}"
NEWSLETTER_SOURCE_DIR="${NEWSLETTER_SOURCE_DIR:-$HOME/Desktop/paper-wiki/newsletter-exports}"
NEWSLETTER_SOURCE="${NEWSLETTER_SOURCE:-}"
NEWSLETTER_SENDER_ALLOWLIST="${NEWSLETTER_SENDER_ALLOWLIST:-newsletter,research,arxiv,substack,medium,openai,deepmind,anthropic,semanticscholar,paperswithcode,alpha signal,import ai,the batch,latent space}"
NEWSLETTER_MAX_MESSAGES="${NEWSLETTER_MAX_MESSAGES:-500}"
NEWSLETTER_MAX_SOURCE_BYTES="${NEWSLETTER_MAX_SOURCE_BYTES:-26214400}"
RUN_DATE="${RUN_DATE:-$(date +%Y-%m-%d)}"

mkdir -p "$NEWSLETTER_SOURCE_DIR" "$WIKI_ROOT/raw/newsletters" "$WIKI_ROOT/pages" "$FALLBACK_WIKI_ROOT/newsletter-exports"

resolve_source() {
  if [ -n "$NEWSLETTER_SOURCE" ]; then
    printf '%s\n' "$NEWSLETTER_SOURCE"
    return 0
  fi

  # Prefer newest explicit exports in the drop folder. Keep one-source behavior
  # because newsletter_ingest.py writes one idempotent page per date.
  find "$NEWSLETTER_SOURCE_DIR" -maxdepth 1 -type f \( -iname '*.mbox' -o -iname '*.mbx' -o -iname '*.jsonl' -o -iname '*.ndjson' \) -print 2>/dev/null \
    | while IFS= read -r f; do printf '%s\t%s\n' "$(stat -f '%m' "$f" 2>/dev/null || stat -c '%Y' "$f")" "$f"; done \
    | sort -nr \
    | head -1 \
    | cut -f2-
}

SOURCE_PATH="$(resolve_source || true)"
if [ -z "$SOURCE_PATH" ] || [ ! -f "$SOURCE_PATH" ]; then
  echo "newsletter ingest skipped: no .mbox/.jsonl export found in $NEWSLETTER_SOURCE_DIR"
  echo "drop exports here: $NEWSLETTER_SOURCE_DIR"
  exit 0
fi

echo "newsletter ingest source: $SOURCE_PATH"
python3 "$SKILL_DIR/newsletter_ingest.py" \
  --source "$SOURCE_PATH" \
  --wiki-root "$WIKI_ROOT" \
  --date "$RUN_DATE" \
  --sender-allowlist "$NEWSLETTER_SENDER_ALLOWLIST" \
  --max-messages "$NEWSLETTER_MAX_MESSAGES" \
  --max-source-bytes "$NEWSLETTER_MAX_SOURCE_BYTES"
