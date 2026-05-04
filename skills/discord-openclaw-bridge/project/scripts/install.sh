#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/discord-openclaw-bridge.service"
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
  echo "Created $PROJECT_DIR/.env; set DISCORD_BOT_TOKEN before starting the service." >&2
fi

if [ -n "$UV_BIN" ]; then
  "$UV_BIN" venv --clear --python 3.11 .venv
  "$UV_BIN" pip install --python .venv/bin/python -e .
else
  $PYTHON_BIN -m venv .venv
  . .venv/bin/activate
  python -m pip install --upgrade pip
  python -m pip install -e .
fi

mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<SERVICE
[Unit]
Description=Discord bridge for loopback OpenClaw gateway
After=network-online.target openclaw-gateway.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/discord-openclaw-bridge
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$PROJECT_DIR

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user enable discord-openclaw-bridge.service

echo "Installed $SERVICE_FILE"
echo "Next: edit $PROJECT_DIR/.env, then run: systemctl --user start discord-openclaw-bridge.service"
