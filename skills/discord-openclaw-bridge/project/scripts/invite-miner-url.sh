#!/usr/bin/env bash
set -euo pipefail
CLIENT_ID="${1:-${DISCORD_MINER_CLIENT_ID:-}}"
if [ -z "$CLIENT_ID" ] && [ -f "$(dirname "$0")/../.env" ]; then
  CLIENT_ID="$(grep -E '^DISCORD_MINER_CLIENT_ID=' "$(dirname "$0")/../.env" | tail -1 | cut -d= -f2- || true)"
fi
if [ -z "$CLIENT_ID" ]; then
  echo "usage: $0 CLIENT_ID" >&2
  echo "or set DISCORD_MINER_CLIENT_ID in project/.env" >&2
  exit 2
fi
# Minimal bot permissions: View Channel, Send Messages, Read Message History, Use Application Commands.
# If DISCORD_MINER_ENABLE_CHANNEL_COLLECTION=1, also enable MESSAGE CONTENT INTENT in the Discord Developer Portal.
PERMISSIONS=2147552256
SCOPES='bot%20applications.commands'
printf 'https://discord.com/oauth2/authorize?client_id=%s&scope=%s&permissions=%s\n' "$CLIENT_ID" "$SCOPES" "$PERMISSIONS"
