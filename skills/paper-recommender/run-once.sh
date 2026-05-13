#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

escaped=""
for a in "$@"; do
  escaped+=" $(printf '%q' "$a")"
done
# Expand ~ on the remote shell. Single-quoting the path would freeze the
# tilde literal, so we let the remote shell expand it but still keep arg
# quoting via the per-argument %q escaping above.
ssh -i "$KEY_FILE" "$REMOTE_HOST" "bash $REMOTE_PROJECT/scripts/run_daily.sh$escaped"

bash "$SKILL_DIR/sync-results.sh"
