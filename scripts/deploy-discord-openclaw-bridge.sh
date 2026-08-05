#!/usr/bin/env bash
set -euo pipefail

echo "WARNING: deploy-discord-openclaw-bridge.sh is a compatibility alias; deploying to Hermes only." >&2
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy-discord-hermes-bridge.sh" "$@"
