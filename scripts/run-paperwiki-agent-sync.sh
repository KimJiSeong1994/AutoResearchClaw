#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export KEY_FILE="${KEY_FILE:-$HOME/.ssh/jiphyeonjeon.pem}"
export REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
export WIKI_ROOT="${WIKI_ROOT:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/PaperWiki/PaperWiki}"
export LOCAL_WEEKLY_ROOT="${LOCAL_WEEKLY_ROOT:-$WIKI_ROOT/raw/weekly}"

exec "$REPO_ROOT/skills/paper-recommender/sync-results.sh"
