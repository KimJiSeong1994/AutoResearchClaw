#!/usr/bin/env bash
set -euo pipefail
CLIENT_ID="${1:-${DISCORD_CLIENT_ID:-}}"
if [ -z "$CLIENT_ID" ] && [ -f "$(dirname "$0")/../.env" ]; then
  CLIENT_ID="$(grep -E '^DISCORD_CLIENT_ID=' "$(dirname "$0")/../.env" | tail -1 | cut -d= -f2- || true)"
fi
if [ -z "$CLIENT_ID" ]; then
  echo "usage: $0 CLIENT_ID" >&2
  exit 2
fi
# Minimal bot permissions: View Channel, Send Messages, Embed Links, Read Message History, Use Application Commands.
PERMISSIONS=2147568640
SCOPES='bot%20applications.commands'
printf 'https://discord.com/oauth2/authorize?client_id=%s&scope=%s&permissions=%s\n' "$CLIENT_ID" "$SCOPES" "$PERMISSIONS"
