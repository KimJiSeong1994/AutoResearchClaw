#!/usr/bin/env bash
set -euo pipefail
systemctl --user restart discord-jiphyeonjeon-miner.service
systemctl --user status discord-jiphyeonjeon-miner.service --no-pager -l | sed -n '1,60p'
