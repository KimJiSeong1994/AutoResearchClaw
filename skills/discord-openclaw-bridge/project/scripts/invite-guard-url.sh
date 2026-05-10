#!/usr/bin/env bash
# Print the OAuth invite URL for the 집현전-경비원 bot (Jiphyeonjeon-Guard).
#
# Usage:
#   bash scripts/invite-guard-url.sh CLIENT_ID
#   bash scripts/invite-guard-url.sh         # reads DISCORD_GUARD_CLIENT_ID from project/.env
set -euo pipefail
CLIENT_ID="${1:-${DISCORD_GUARD_CLIENT_ID:-}}"
if [ -z "$CLIENT_ID" ] && [ -f "$(dirname "$0")/../.env" ]; then
  CLIENT_ID="$(grep -E '^DISCORD_GUARD_CLIENT_ID=' "$(dirname "$0")/../.env" | tail -1 | cut -d= -f2- || true)"
fi
if [ -z "$CLIENT_ID" ]; then
  echo "usage: $0 CLIENT_ID" >&2
  echo "or set DISCORD_GUARD_CLIENT_ID in project/.env" >&2
  exit 2
fi
# Permissions for forum posting only:
#   View Channel (1024)
#   Send Messages (2048)
#   Send Messages in Threads (274877906944)
#   Create Public Threads (34359738368)
#   Read Message History (65536)
# Sum: 274877906944 + 34359738368 + 1024 + 2048 + 65536 = 309238716920
PERMISSIONS=309238716920
SCOPES='bot'
printf 'https://discord.com/oauth2/authorize?client_id=%s&scope=%s&permissions=%s\n' "$CLIENT_ID" "$SCOPES" "$PERMISSIONS"
