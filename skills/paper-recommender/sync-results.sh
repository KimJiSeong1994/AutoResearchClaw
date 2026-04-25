#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_ARTIFACTS="${REMOTE_ARTIFACTS:-~/.openclaw/workspace/projects/paper-recommender/artifacts/}"
REMOTE_FEEDBACK_INBOX="${REMOTE_FEEDBACK_INBOX:-~/.openclaw/workspace/projects/paper-recommender/state/feedback_inbox/}"
LOCAL_ROOT="${LOCAL_ROOT:-/Users/jiseong/Library/Mobile Documents/iCloud~md~obsidian/Documents/Write Paper/AutoResearchClaw/recommendations}"
FEEDBACK_LOOKBACK_DAYS="${FEEDBACK_LOOKBACK_DAYS:-7}"
FEEDBACK_MAX_BYTES="${FEEDBACK_MAX_BYTES:-524288}"   # 512 KB

mkdir -p "$LOCAL_ROOT"
# Use double quotes only around the whole command so ~ expands on the remote
# shell. (Inner single quotes would freeze the tilde literal — same bug class
# as the SCP path.)
ssh -i "$KEY_FILE" "$REMOTE_HOST" "mkdir -p $REMOTE_ARTIFACTS $REMOTE_FEEDBACK_INBOX"

# 1. Pull artifacts EC2 -> Obsidian (forward sync, with safety flags).
rsync -az --safe-links --max-size=10M \
  -e "ssh -i $KEY_FILE" \
  "${REMOTE_HOST}:${REMOTE_ARTIFACTS}" \
  "$LOCAL_ROOT/"

echo "synced recommendations to:"
echo "  $LOCAL_ROOT"

# 2. Push the last N days of recommendations.md back to EC2 feedback_inbox.
#    Defenses: filename allowlist (single basename), size cap, symlink rejection,
#    realpath containment check, iCloud placeholder skip.
date_at_offset() {
  local n="$1"
  if date -v-1d +%Y-%m-%d >/dev/null 2>&1; then
    date -v-"$n"d +%Y-%m-%d
  else
    date -d "$n days ago" +%Y-%m-%d
  fi
}

# realpath -m exists on Linux; on macOS we need the python fallback.
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
  src="$LOCAL_ROOT/$d/recommendations.md"
  dir="$LOCAL_ROOT/$d"

  # Skip iCloud-not-yet-downloaded placeholders.
  if [ -f "$src.icloud" ] || [ -f "$dir/.${d}.icloud" ]; then
    echo "skip: $d note still downloading from iCloud"
    continue
  fi

  if [ ! -e "$src" ]; then
    continue
  fi

  # Reject symlinks at either the date dir or the file itself.
  if [ -L "$dir" ] || [ -L "$src" ]; then
    echo "skip: $d (symlink rejected)" >&2
    continue
  fi
  if [ ! -f "$src" ]; then
    continue
  fi

  # Containment check: realpath must stay under LOCAL_ROOT_REAL.
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
    echo "skip oversized feedback note: $d.md ($size bytes > $FEEDBACK_MAX_BYTES)"
    continue
  fi

  # Use ssh-tunneled write instead of scp: scp via SFTP does not expand ~ on
  # the remote, and shell-quoting the remote path freezes the tilde literal.
  # ssh "cmd" goes through the remote login shell, which does expand ~.
  if ssh -i "$KEY_FILE" "$REMOTE_HOST" "cat > $REMOTE_FEEDBACK_INBOX${d}.md" < "$src"; then
    pushed=$((pushed + 1))
  else
    echo "scp failed for $d.md" >&2
  fi
done
echo "pushed $pushed note(s) to feedback inbox: ${REMOTE_FEEDBACK_INBOX}"
