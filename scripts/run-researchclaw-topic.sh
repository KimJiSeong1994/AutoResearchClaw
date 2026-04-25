#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 \"research topic\"" >&2
  exit 1
fi

KEY_FILE="/Users/jiseong/git/PaperReviewAgent/jiseong.pem"
REMOTE_HOST="ubuntu@52.79.96.56"
TOPIC="$*"

ssh -i "$KEY_FILE" "$REMOTE_HOST" "bash ~/.openclaw/workspace/skills/researchclaw/run-topic.sh \"$TOPIC\""

bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sync-researchclaw-results.sh"
