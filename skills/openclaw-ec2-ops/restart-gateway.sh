#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.npm-global/bin:$PATH"
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
export OPENCLAW_NO_RESPAWN=1

echo "== restarting gateway =="
openclaw gateway restart
echo
echo "== post-restart status =="
openclaw gateway status
