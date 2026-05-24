#!/usr/bin/env bash
set -euo pipefail
CLIENT_ID="${1:-${DISCORD_REPORTER_CLIENT_ID:-}}"
if [ -z "$CLIENT_ID" ] && [ -f "$(dirname "$0")/../.env" ]; then
  CLIENT_ID="$(grep -E '^DISCORD_REPORTER_CLIENT_ID=' "$(dirname "$0")/../.env" | tail -1 | cut -d= -f2- || true)"
fi
if [ -z "$CLIENT_ID" ]; then
  echo "usage: $0 CLIENT_ID" >&2
  echo "or set DISCORD_REPORTER_CLIENT_ID in project/.env" >&2
  exit 2
fi
# Minimal bot permissions: View Channel, Send Messages, Create Public Threads, Send Messages in Threads, Read Message History, Use Application Commands.
PERMISSIONS=2149649440
SCOPES='bot%20applications.commands'
printf 'https://discord.com/oauth2/authorize?client_id=%s&scope=%s&permissions=%s\n' "$CLIENT_ID" "$SCOPES" "$PERMISSIONS"
