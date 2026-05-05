#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/discord-jiphyeonjeon-miner.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"
UV_BIN="${UV_BIN:-}"
if [ -z "$UV_BIN" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
  elif [ -x "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
  fi
fi

cd "$PROJECT_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created $PROJECT_DIR/.env; set DISCORD_MINER_BOT_TOKEN before starting the service." >&2
fi

if [ -n "$UV_BIN" ]; then
  if [ ! -x .venv/bin/python ]; then
    "$UV_BIN" venv --python 3.11 .venv
  fi
  "$UV_BIN" pip install --python .venv/bin/python -e .
else
  if [ ! -x .venv/bin/python ]; then
    $PYTHON_BIN -m venv .venv
  fi
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e .
fi

mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Discord bot for Jiphyeonjeon-Miner link intake
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/discord-jiphyeonjeon-miner
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$PROJECT_DIR $HOME/.openclaw/workspace

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable discord-jiphyeonjeon-miner.service

echo "Installed $SERVICE_FILE"
echo "Next: edit $PROJECT_DIR/.env, then run: systemctl --user start discord-jiphyeonjeon-miner.service"
