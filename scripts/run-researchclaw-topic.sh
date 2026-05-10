#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 \"research topic\"" >&2
  exit 1
fi

KEY_FILE="/Users/jiseong/git/PaperReviewAgent/jiseong.pem"
REMOTE_HOST="ubuntu@52.79.96.56"
TOPIC="$*"
TOPIC_B64="$(printf '%s' "$TOPIC" | base64 | tr -d '\n')"

ssh -i "$KEY_FILE" "$REMOTE_HOST" bash -s -- "$TOPIC_B64" <<'REMOTE_SCRIPT'
set -euo pipefail

TOPIC="$(printf '%s' "$1" | python3 -c 'import base64, sys; print(base64.b64decode(sys.stdin.read()).decode(), end="")')"
exec bash ~/.openclaw/workspace/skills/researchclaw/run-topic.sh "$TOPIC"
REMOTE_SCRIPT

bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sync-researchclaw-results.sh"
