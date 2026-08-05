#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
HERMES_WORKSPACE="${HERMES_WORKSPACE:-~/.hermes/workspace}"
OPENCLAW_BRIDGE_ENV="${OPENCLAW_BRIDGE_ENV:-~/.openclaw/workspace/skills/discord-openclaw-bridge/project/.env}"
HERMES_BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}"
HERMES_TOKEN_FILE="${HERMES_GATEWAY_TOKEN_FILE:-~/.hermes_gateway_token}"
HERMES_MODEL="${HERMES_MODEL:-hermes-agent}"
HERMES_BRIDGE_SERVICE="${HERMES_BRIDGE_SERVICE:-discord-hermes-bridge.service}"
case "$HERMES_WORKSPACE" in
  "~/.hermes/"*|"~/.hermes") ;;
  *) echo "FAIL: HERMES_WORKSPACE must stay under ~/.hermes" >&2; exit 1 ;;
esac
case "$HERMES_WORKSPACE" in
  *"'"*) echo "FAIL: HERMES_WORKSPACE contains unsafe shell characters" >&2; exit 1 ;;
esac
case "$HERMES_WORKSPACE" in
  *"/../"*|*"../"*|*".."|*"/..") echo "FAIL: HERMES_WORKSPACE must not contain parent-directory traversal" >&2; exit 1 ;;
esac
if [[ ! "$HERMES_BASE_URL" =~ ^http://(127\.0\.0\.1|localhost):[0-9]+(/.*)?$ ]]; then
  echo "FAIL: HERMES_BASE_URL must remain strict loopback" >&2
  exit 1
fi
[[ "$HERMES_BRIDGE_SERVICE" == "discord-hermes-bridge.service" ]] || {
  echo "FAIL: HERMES_BRIDGE_SERVICE must be discord-hermes-bridge.service" >&2
  exit 1
}

SSH_OPTS=(
  -i "$KEY_FILE"
  -o BatchMode=yes
  -o ConnectTimeout="${SSH_CONNECT_TIMEOUT:-10}"
  -o ServerAliveInterval="${SSH_SERVER_ALIVE_INTERVAL:-15}"
  -o ServerAliveCountMax="${SSH_SERVER_ALIVE_COUNT_MAX:-2}"
)
if [[ -n "${SSH_OPTIONS:-}" ]]; then
  # shellcheck disable=SC2206
  SSH_EXTRA_OPTIONS=(${SSH_OPTIONS})
  SSH_OPTS+=("${SSH_EXTRA_OPTIONS[@]}")
fi
quote_remote() { printf '%q' "$1"; }

ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" \
  "HERMES_WORKSPACE=$(quote_remote "$HERMES_WORKSPACE") OPENCLAW_BRIDGE_ENV=$(quote_remote "$OPENCLAW_BRIDGE_ENV") HERMES_BASE_URL=$(quote_remote "$HERMES_BASE_URL") HERMES_TOKEN_FILE=$(quote_remote "$HERMES_TOKEN_FILE") HERMES_MODEL=$(quote_remote "$HERMES_MODEL") HERMES_BRIDGE_SERVICE=$(quote_remote "$HERMES_BRIDGE_SERVICE") bash -s" <<'REMOTE'
set -euo pipefail
workspace="${HERMES_WORKSPACE/#\~/$HOME}"
source_env="${OPENCLAW_BRIDGE_ENV/#\~/$HOME}"
project="$workspace/skills/discord-openclaw-bridge/project"
bridge_env="$project/.env"
token_file="${HERMES_TOKEN_FILE/#\~/$HOME}"
service_dir="$HOME/.config/systemd/user"
service_file="$service_dir/$HERMES_BRIDGE_SERVICE"
old_openclaw="discord-openclaw-bridge.service"
old_canary="discord-hermes-bridge-canary.service"

[ -d "$project" ] || { echo "FAIL: Hermes bridge project missing" >&2; exit 1; }
[ -f "$token_file" ] || { echo "FAIL: Hermes gateway token file missing" >&2; exit 1; }
[ -f "$bridge_env" ] || [ -f "$source_env" ] || { echo "FAIL: no bridge environment is available" >&2; exit 1; }
mkdir -p "$service_dir" "$HOME/.hermes/state"

python3 - "$source_env" "$bridge_env" "$HERMES_BASE_URL" "$token_file" "$HERMES_MODEL" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
base_url, token_file, model = sys.argv[3:]
origin = target if target.is_file() else source
lines = origin.read_text(encoding="utf-8").splitlines()
updates = {
    "HERMES_BASE_URL": base_url,
    "HERMES_GATEWAY_TOKEN_FILE": token_file,
    "HERMES_MODEL": model,
    "OPENCLAW_BASE_URL": base_url,
    "OPENCLAW_GATEWAY_TOKEN_FILE": token_file,
    "OPENCLAW_MODEL": model,
}
replacements = {
    "/home/ubuntu/.openclaw/workspace": "/home/ubuntu/.hermes/workspace",
    "/home/ubuntu/.openclaw/state": "/home/ubuntu/.hermes/state",
    "/home/ubuntu/.openclaw_gateway_token": token_file,
}
out, seen = [], set()
for line in lines:
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        out.append(line)
        continue
    key, value = line.split("=", 1)
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
        continue
    for old, new in replacements.items():
        value = value.replace(old, new)
    out.append(f"{key}={value}")
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
target.write_text("\n".join(out) + "\n", encoding="utf-8")
target.chmod(0o600)
PY

if [ -x "$project/.venv/bin/python" ]; then
  python_bin="$project/.venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
  (cd "$project" && uv venv --python 3.11 .venv >/dev/null && uv pip install --python .venv/bin/python -e . >/dev/null)
  python_bin="$project/.venv/bin/python"
elif [ -x "$HOME/.local/bin/uv" ]; then
  (cd "$project" && "$HOME/.local/bin/uv" venv --python 3.11 .venv >/dev/null && "$HOME/.local/bin/uv" pip install --python .venv/bin/python -e . >/dev/null)
  python_bin="$project/.venv/bin/python"
else
  echo "FAIL: uv is required to create the Hermes bridge environment" >&2
  exit 1
fi

cat > "$service_file" <<SERVICE
[Unit]
Description=Discord bridge for Hermes gateway
After=network-online.target hermes-gateway.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$project
EnvironmentFile=$bridge_env
Environment=PYTHONPATH=$project/src
ExecStart=$python_bin -m discord_openclaw_bridge.bot
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$project $workspace $HOME/.hermes/state

[Install]
WantedBy=default.target
SERVICE

systemctl --user is-active --quiet hermes-gateway.service || { echo "FAIL: hermes-gateway.service is not active" >&2; exit 1; }
curl --fail --silent --show-error --max-time 20 "${HERMES_BASE_URL%/v1}/health" >/dev/null
old_openclaw_active="$(systemctl --user is-active "$old_openclaw" 2>/dev/null || true)"
old_canary_active="$(systemctl --user is-active "$old_canary" 2>/dev/null || true)"
rollback() {
  echo "FAIL: Hermes primary bridge did not become ready; restoring the previous bridge" >&2
  systemctl --user stop "$HERMES_BRIDGE_SERVICE" 2>/dev/null || true
  if [ "$old_canary_active" = active ]; then
    systemctl --user start "$old_canary" 2>/dev/null || true
  elif [ "$old_openclaw_active" = active ]; then
    systemctl --user start openclaw-gateway.service 2>/dev/null || true
    systemctl --user start "$old_openclaw" 2>/dev/null || true
  fi
}
trap rollback ERR
systemctl --user daemon-reload
systemctl --user stop "$old_openclaw" "$old_canary" "$HERMES_BRIDGE_SERVICE" 2>/dev/null || true
marker="$(date -u +%Y-%m-%d\ %H:%M:%S)"
systemctl --user start "$HERMES_BRIDGE_SERVICE"
sleep 45
systemctl --user is-active --quiet "$HERMES_BRIDGE_SERVICE"
journalctl --user -u "$HERMES_BRIDGE_SERVICE" --since "$marker UTC" --no-pager \
  | grep -E "ready user=|connected to Gateway" >/dev/null
if journalctl --user -u "$HERMES_BRIDGE_SERVICE" --since "$marker UTC" --no-pager \
  | grep -Ei "traceback|exception|login failure|improper token|401|403|failed" >/dev/null; then
  false
fi
systemctl --user enable "$HERMES_BRIDGE_SERVICE" >/dev/null
systemctl --user disable "$old_openclaw" "$old_canary" >/dev/null 2>&1 || true
systemctl --user disable --now openclaw-gateway.service >/dev/null 2>&1 || true
systemctl --user reset-failed openclaw-gateway.service >/dev/null 2>&1 || true

if crontab -l >/dev/null 2>&1; then
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  crontab -l | sed 's#/home/ubuntu/.openclaw/workspace/scripts/miner-seeds.sh#/home/ubuntu/.hermes/workspace/scripts/miner-seeds.sh#g' > "$tmp"
  crontab "$tmp"
fi
trap - ERR
echo "Hermes primary bridge is active; OpenClaw services are disabled for rollback-only retention."
systemctl --user show "$HERMES_BRIDGE_SERVICE" \
  -p ActiveState -p SubState -p ExecMainStatus -p NRestarts --no-pager
REMOTE
