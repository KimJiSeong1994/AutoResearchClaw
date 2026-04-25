#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.npm-global/bin:$PATH"
export NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache
export OPENCLAW_NO_RESPAWN=1

echo "== openclaw gateway status =="
openclaw gateway status
echo
echo "== listeners =="
ss -ltnp | grep -E '(:18789|:18791)\b' || true
