#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="/Users/jiseong/git/PaperReviewAgent/jiseong.pem"
REMOTE_HOST="ubuntu@52.79.96.56"

echo "Opening SSH tunnel: http://127.0.0.1:18789 -> ${REMOTE_HOST}:127.0.0.1:18789"
exec ssh -N -L 18789:127.0.0.1:18789 -i "$KEY_FILE" "$REMOTE_HOST"
