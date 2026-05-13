#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_ARTIFACTS="${REMOTE_ARTIFACTS:-~/.openclaw/workspace/projects/paper-recommender/artifacts/}"
REMOTE_WEEKLY_ARTIFACTS="${REMOTE_WEEKLY_ARTIFACTS:-${REMOTE_ARTIFACTS%/}/weekly/}"
REMOTE_FEEDBACK_INBOX="${REMOTE_FEEDBACK_INBOX:-~/.openclaw/workspace/projects/paper-recommender/state/feedback_inbox/}"

# Merged into the existing PaperWiki vault — flat-with-prefix style on pages/
# and date-folder rsync mirror under the top-level raw/.
#
#   {WIKI_ROOT}/raw/{date}/daily-research.md           ← rsync from EC2
#   {WIKI_ROOT}/pages/autoresearch-index.md            ← catalog
#   {WIKI_ROOT}/pages/autoresearch-{date}.md           ← daily entry
#   {WIKI_ROOT}/pages/autoresearch-{date}-papers.md    ← paper cards
#   {WIKI_ROOT}/pages/autoresearch-topic-{slug}.md     ← topic, append-mode
WIKI_ROOT="${WIKI_ROOT:?Set WIKI_ROOT to the local PaperWiki root}"
# autoresearch raw lives under its own raw subdir (papers/ and reviews/ are
# managed by the bookmark pipeline and shouldn't intermix with the date-folder
# autoresearch outputs).
LOCAL_ROOT="${LOCAL_ROOT:-${WIKI_ROOT}/raw/autoresearch}"
# Weekly reports continue to land in the legacy PaperReview vault for now.
LOCAL_WEEKLY_ROOT="${LOCAL_WEEKLY_ROOT:?Set LOCAL_WEEKLY_ROOT to the local weekly output directory}"
FEEDBACK_LOOKBACK_DAYS="${FEEDBACK_LOOKBACK_DAYS:-7}"
FEEDBACK_MAX_BYTES="${FEEDBACK_MAX_BYTES:-524288}"

WIKI_PUBLISH_PY="${WIKI_PUBLISH_PY:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/wiki_publish.py}"

mkdir -p "$LOCAL_ROOT" "$LOCAL_WEEKLY_ROOT" "$WIKI_ROOT/pages"
ssh -i "$KEY_FILE" "$REMOTE_HOST" "mkdir -p $REMOTE_ARTIFACTS $REMOTE_FEEDBACK_INBOX"

# 1. Pull daily artifacts EC2 -> autoresearch/raw/ (verbatim mirror).
# Excludes:
#   /weekly/    — weekly trend reports go to a separate Obsidian vault (Phase 2)
#   profile.md  — pipeline-internal user-profile JSON-rendered-to-md, not wiki content
#   souls/      — pipeline-internal SOUL profile dir, not wiki content
#   *.icloud    — iCloud not-yet-downloaded placeholders
rsync -az --safe-links --max-size=10M \
  --exclude '/weekly/' \
  --exclude 'profile.md' \
  --exclude 'souls/' \
  --exclude '*.icloud' \
  -e "ssh -i $KEY_FILE" \
  "${REMOTE_HOST}:${REMOTE_ARTIFACTS}" \
  "$LOCAL_ROOT/"

echo "synced raw artifacts to:"
echo "  $LOCAL_ROOT"

# 1.5 Decompose each newly-synced daily-research.md into wiki pages.
# wiki_publish.py is idempotent — it overwrites today's daily entry and the
# corresponding topic-page sections, accumulating other days untouched.
if [ ! -f "$WIKI_PUBLISH_PY" ]; then
  echo "warn: wiki_publish.py not found at $WIKI_PUBLISH_PY — skipping wiki step" >&2
else
  published=0
  for note in "$LOCAL_ROOT"/*/daily-research.md; do
    [ -e "$note" ] || continue
    if python3 "$WIKI_PUBLISH_PY" "$note" "$WIKI_ROOT" >/dev/null; then
      published=$((published + 1))
    else
      echo "wiki_publish failed for $note" >&2
    fi
  done
  echo "published $published daily-research notes to wiki:"
  echo "  $WIKI_ROOT/pages/  (autoresearch-index, autoresearch-{date}, autoresearch-topic-*)"
fi

# 1.7 Fetch PDFs for autoresearch-recommended papers into raw/papers/.
PDF_FETCH_PY="${PDF_FETCH_PY:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/autoresearch_pdf_fetch.py}"
if [ -f "$PDF_FETCH_PY" ]; then
  echo "fetching PDFs for autoresearch papers (latest date only)..."
  python3 "$PDF_FETCH_PY" "$WIKI_ROOT" 2>&1 | tail -8
else
  echo "warn: autoresearch_pdf_fetch.py not found at $PDF_FETCH_PY — skipping PDF fetch"
fi

# 2. Pull weekly trend reports EC2 -> Obsidian PaperReview vault (unchanged).
if ssh -i "$KEY_FILE" "$REMOTE_HOST" "test -d ${REMOTE_WEEKLY_ARTIFACTS}"; then
  rsync -az --safe-links --max-size=10M \
    -e "ssh -i $KEY_FILE" \
    "${REMOTE_HOST}:${REMOTE_WEEKLY_ARTIFACTS}" \
    "$LOCAL_WEEKLY_ROOT/"
  echo "synced weekly reports to:"
  echo "  $LOCAL_WEEKLY_ROOT"
else
  echo "no weekly reports on remote yet: ${REMOTE_WEEKLY_ARTIFACTS}"
  echo "weekly reports will sync to:"
  echo "  $LOCAL_WEEKLY_ROOT"
fi

# 3. Push the last N days' recommendations.md (legacy) AND daily-research.md
#    (new) back to EC2 feedback_inbox so the existing feedback parser can
#    consume markers like [read] / [dislike: X].
date_at_offset() {
  local n="$1"
  if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
    date -v-"$n"d +%Y-%m-%d
  else
    date -d "$n days ago" +%Y-%m-%d
  fi
}

canonical_path() {
  local p="$1"
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$p" 2>/dev/null || python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$p"
  else
    python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$p"
  fi
}

LOCAL_ROOT_REAL="$(canonical_path "$LOCAL_ROOT")"

pushed=0
for n in $(seq 0 "$FEEDBACK_LOOKBACK_DAYS"); do
  d="$(date_at_offset "$n")"
  dir="$LOCAL_ROOT/$d"

  for fname in recommendations.md daily-research.md; do
    src="$dir/$fname"

    if [ -f "$src.icloud" ] || [ -f "$dir/.${d}.icloud" ]; then
      echo "skip: $d $fname still downloading from iCloud"
      continue
    fi

    [ -e "$src" ] || continue

    if [ -L "$dir" ] || [ -L "$src" ]; then
      echo "skip: $d $fname (symlink rejected)" >&2
      continue
    fi
    [ -f "$src" ] || continue

    src_real="$(canonical_path "$src")"
    case "$src_real" in
      "$LOCAL_ROOT_REAL"/*) ;;
      *)
        echo "skip: $src resolves outside LOCAL_ROOT" >&2
        continue
        ;;
    esac

    size="$(wc -c < "$src" | tr -d ' ')"
    if [ "$size" -gt "$FEEDBACK_MAX_BYTES" ]; then
      echo "skip oversized feedback note: $d/$fname ($size bytes > $FEEDBACK_MAX_BYTES)"
      continue
    fi

    # Filename in the feedback inbox encodes both date and source so the
    # parser can disambiguate legacy vs daily-research feedback streams.
    remote_name="${d}-${fname}"
    if ssh -i "$KEY_FILE" "$REMOTE_HOST" "cat > $REMOTE_FEEDBACK_INBOX${remote_name}" < "$src"; then
      pushed=$((pushed + 1))
    else
      echo "ssh push failed for $d/$fname" >&2
    fi
  done
done
echo "pushed $pushed note(s) to feedback inbox: ${REMOTE_FEEDBACK_INBOX}"
