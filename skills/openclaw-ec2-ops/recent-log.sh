#!/usr/bin/env bash
set -euo pipefail

if compgen -G "/tmp/openclaw/*.log" > /dev/null; then
  latest_log="$(ls -1t /tmp/openclaw/*.log | head -n 1)"
  echo "== latest runtime log: ${latest_log} =="
  tail -n 120 "$latest_log"
else
  echo "No runtime log found under /tmp/openclaw"
fi
