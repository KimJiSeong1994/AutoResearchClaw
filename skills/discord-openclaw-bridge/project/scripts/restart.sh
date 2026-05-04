#!/usr/bin/env bash
set -euo pipefail
systemctl --user restart discord-openclaw-bridge.service
systemctl --user status discord-openclaw-bridge.service --no-pager -l | sed -n '1,60p'
