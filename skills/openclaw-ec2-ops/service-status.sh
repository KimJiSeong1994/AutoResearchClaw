#!/usr/bin/env bash
set -euo pipefail

echo "== systemd user service =="
systemctl --user status openclaw-gateway.service --no-pager -l | sed -n '1,120p'
