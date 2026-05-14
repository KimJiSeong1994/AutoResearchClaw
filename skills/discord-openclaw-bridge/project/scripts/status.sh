#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "== services =="
for service in discord-openclaw-bridge.service discord-jiphyeonjeon-miner.service discord-jiphyeonjeon-traveler.service; do
  echo "-- $service --"
  systemctl --user status "$service" --no-pager -l 2>&1 | sed -n '1,80p' || true
done

echo "== safe config =="
if [ -f .env ]; then
  grep -E '^(DISCORD_CLIENT_ID|DISCORD_MINER_CLIENT_ID|DISCORD_TRAVELER_CLIENT_ID|DISCORD_GUARD_CLIENT_ID|DISCORD_GUILD_ID|DISCORD_ALLOWED_CHANNEL_ID|DISCORD_MINER_CHANNEL_ID|DISCORD_TRAVELER_CHANNEL_ID|DISCORD_OPS_REPORT_CHANNEL_ID|OPENCLAW_BASE_URL|OPENCLAW_MODEL|DISCORD_ENABLE_MENTION_RESPONSES|DISCORD_MINER_ENABLE_CHANNEL_COLLECTION|DISCORD_BRIEFING_SOURCE|JIPHYEONJEON_MINER_INTAKE_PATH|JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH|JIPHYEONJEON_MINER_DECISIONS_PATH|JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH|JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH|MINER_SEEDS_STATUS_PATH)=' .env || true
  if grep -q '^DISCORD_BOT_TOKEN=.' .env; then echo 'DISCORD_BOT_TOKEN=set'; else echo 'DISCORD_BOT_TOKEN=missing'; fi
  if grep -q '^DISCORD_MINER_BOT_TOKEN=.' .env; then echo 'DISCORD_MINER_BOT_TOKEN=set'; else echo 'DISCORD_MINER_BOT_TOKEN=missing'; fi
  if grep -q '^DISCORD_TRAVELER_BOT_TOKEN=.' .env; then echo 'DISCORD_TRAVELER_BOT_TOKEN=set'; else echo 'DISCORD_TRAVELER_BOT_TOKEN=missing'; fi
  if grep -q '^DISCORD_GUARD_BOT_TOKEN=.' .env; then echo 'DISCORD_GUARD_BOT_TOKEN=set'; else echo 'DISCORD_GUARD_BOT_TOKEN=missing'; fi
else
  echo '.env missing'
fi

echo "== guard ops digest =="
if [ -x .venv/bin/discord-openclaw-guard-ops-digest ]; then
  .venv/bin/discord-openclaw-guard-ops-digest --env-path .env || true
else
  echo 'guard ops digest: skipped (.venv entrypoint missing)'
fi

echo "== openclaw loopback =="
if command -v curl >/dev/null 2>&1 && [ -f "${OPENCLAW_GATEWAY_TOKEN_FILE:-$HOME/.openclaw_gateway_token}" ]; then
  token_file="${OPENCLAW_GATEWAY_TOKEN_FILE:-$HOME/.openclaw_gateway_token}"
  base_url="$(grep -E '^OPENCLAW_BASE_URL=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  base_url="${base_url:-http://127.0.0.1:18789/v1}"
  curl -fsS -H "Authorization: Bearer $(tr -d '\n' < "$token_file")" "$base_url/models" >/dev/null && echo 'models: ok' || echo 'models: FAIL'
else
  echo 'models: skipped (curl or token file missing)'
fi

echo "== recent logs =="
for service in discord-openclaw-bridge.service discord-jiphyeonjeon-miner.service discord-jiphyeonjeon-traveler.service; do
  echo "-- $service --"
  journalctl --user -u "$service" --no-pager -n 40 2>&1 || true
done
