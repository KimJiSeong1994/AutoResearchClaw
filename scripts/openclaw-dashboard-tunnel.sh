#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"

echo "Opening SSH tunnel: http://127.0.0.1:18789 -> ${REMOTE_HOST}:127.0.0.1:18789"
exec ssh -N -L 18789:127.0.0.1:18789 -i "$KEY_FILE" "$REMOTE_HOST"
