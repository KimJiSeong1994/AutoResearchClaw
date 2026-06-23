#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
HERMES_WORKSPACE="${HERMES_WORKSPACE:-~/.hermes/workspace}"
OPENCLAW_BRIDGE_ENV="${OPENCLAW_BRIDGE_ENV:-~/.openclaw/workspace/skills/discord-openclaw-bridge/project/.env}"
HERMES_BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}"
HERMES_TOKEN_FILE="${HERMES_GATEWAY_TOKEN_FILE:-~/.hermes_gateway_token}"
HERMES_MODEL="${HERMES_MODEL:-hermes-agent-canary}"
HERMES_BRIDGE_SERVICE="${HERMES_BRIDGE_SERVICE:-discord-hermes-bridge-canary.service}"
HERMES_BRIDGE_ENABLE_GUARD="${HERMES_BRIDGE_ENABLE_GUARD:-~/.hermes/ENABLE_DISCORD_BRIDGE_CANARY}"
case "$HERMES_WORKSPACE" in
  "~/.hermes/"*|"~/.hermes") ;;
  *) echo "FAIL: HERMES_WORKSPACE must stay under the ~/.hermes canary directory" >&2; exit 1 ;;
esac
case "$HERMES_WORKSPACE" in
  *"'"*) echo "FAIL: HERMES_WORKSPACE contains unsafe shell characters" >&2; exit 1 ;;
esac
case "$HERMES_WORKSPACE" in
  *"/../"*|*"../"*|*".."|*"/..") echo "FAIL: HERMES_WORKSPACE must not contain parent-directory traversal" >&2; exit 1 ;;
esac
if [[ ! "$HERMES_BASE_URL" =~ ^http://(127\.0\.0\.1|localhost):[0-9]+(/.*)?$ ]]; then
  echo "FAIL: HERMES_BASE_URL must remain strict loopback http://127.0.0.1:<port>/... or http://localhost:<port>/..." >&2
  exit 1
fi
case "$HERMES_BRIDGE_SERVICE" in
  discord-hermes-bridge-canary.service) ;;
  *) echo "FAIL: HERMES_BRIDGE_SERVICE must be discord-hermes-bridge-canary.service" >&2; exit 1 ;;
esac
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

echo "== install Hermes bridge canary service =="
echo "host: $REMOTE_HOST"
echo

ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" \
  "HERMES_WORKSPACE=$(quote_remote "$HERMES_WORKSPACE") OPENCLAW_BRIDGE_ENV=$(quote_remote "$OPENCLAW_BRIDGE_ENV") HERMES_BASE_URL=$(quote_remote "$HERMES_BASE_URL") HERMES_TOKEN_FILE=$(quote_remote "$HERMES_TOKEN_FILE") HERMES_MODEL=$(quote_remote "$HERMES_MODEL") HERMES_BRIDGE_SERVICE=$(quote_remote "$HERMES_BRIDGE_SERVICE") HERMES_BRIDGE_ENABLE_GUARD=$(quote_remote "$HERMES_BRIDGE_ENABLE_GUARD") bash -s" <<'REMOTE'
set -euo pipefail
workspace="${HERMES_WORKSPACE/#\~/$HOME}"
source_env="${OPENCLAW_BRIDGE_ENV/#\~/$HOME}"
project_dir="$workspace/skills/discord-openclaw-bridge/project"
bridge_env="$project_dir/.env"
service_dir="$HOME/.config/systemd/user"
service_file="$service_dir/$HERMES_BRIDGE_SERVICE"
guard_file="${HERMES_BRIDGE_ENABLE_GUARD/#\~/$HOME}"
token_file="${HERMES_TOKEN_FILE/#\~/$HOME}"

[ -d "$project_dir" ] || { echo "FAIL: canary bridge project missing" >&2; exit 1; }
[ -f "$source_env" ] || { echo "FAIL: source OpenClaw bridge .env missing" >&2; exit 1; }
[ -f "$token_file" ] || { echo "FAIL: Hermes gateway token file missing" >&2; exit 1; }

mkdir -p "$service_dir" "$(dirname "$guard_file")"
# Merge production bridge environment on-host only, then force Hermes canary gateway aliases.
python3 - "$source_env" "$bridge_env" "$HERMES_BASE_URL" "$token_file" "$HERMES_MODEL" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
base_url = sys.argv[3]
token_file = sys.argv[4]
model = sys.argv[5]
lines = src.read_text(encoding="utf-8").splitlines()
updates = {
    "HERMES_BASE_URL": base_url,
    "HERMES_GATEWAY_TOKEN_FILE": token_file,
    "HERMES_MODEL": model,
    "OPENCLAW_BASE_URL": base_url,
    "OPENCLAW_GATEWAY_TOKEN_FILE": token_file,
    "OPENCLAW_MODEL": model,
}
# Keep production Discord/app env values for future cutover readiness, but point stateful defaults at Hermes.
replacements = {
    "/home/ubuntu/.openclaw/workspace": "/home/ubuntu/.hermes/workspace",
    "/home/ubuntu/.openclaw/state": "/home/ubuntu/.hermes/state",
    "/home/ubuntu/.openclaw_gateway_token": token_file,
}
out = []
seen = set()
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
dst.write_text("\n".join(out) + "\n", encoding="utf-8")
dst.chmod(0o600)
PY

# Ensure import/runtime dependencies without starting the Discord bot.
if [ -x "$project_dir/.venv/bin/python" ]; then
  python_bin="$project_dir/.venv/bin/python"
elif command -v uv >/dev/null 2>&1; then
  (cd "$project_dir" && uv venv --python 3.11 .venv >/dev/null && uv pip install --python .venv/bin/python -e . >/dev/null)
  python_bin="$project_dir/.venv/bin/python"
elif [ -x "$HOME/.local/bin/uv" ]; then
  (cd "$project_dir" && "$HOME/.local/bin/uv" venv --python 3.11 .venv >/dev/null && "$HOME/.local/bin/uv" pip install --python .venv/bin/python -e . >/dev/null)
  python_bin="$project_dir/.venv/bin/python"
else
  python_bin="$HOME/.hermes/hermes-agent/venv/bin/python"
fi

cat > "$service_file" <<SERVICE
[Unit]
Description=Discord bridge for Hermes canary gateway (guarded, disabled)
After=network-online.target hermes-gateway.service
Wants=network-online.target
ConditionPathExists=$guard_file

[Service]
Type=simple
WorkingDirectory=$project_dir
EnvironmentFile=$bridge_env
Environment=PYTHONPATH=$project_dir/src
ExecStart=$python_bin -m discord_openclaw_bridge.bot
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$project_dir $workspace $HOME/.hermes/state

[Install]
WantedBy=default.target
SERVICE

systemctl --user daemon-reload
systemctl --user disable --now "$HERMES_BRIDGE_SERVICE" >/dev/null 2>&1 || true
rm -f "$guard_file"

printf '%s\n' "installed: $service_file"
printf '%s\n' "state: disabled and stopped; guard file absent"
printf '%s\n' "guard: $guard_file"
printf '%s\n' "python: $python_bin"
printf '%s\n' "env aliases:"
grep -E '^(HERMES_BASE_URL|HERMES_GATEWAY_TOKEN_FILE|HERMES_MODEL|OPENCLAW_BASE_URL|OPENCLAW_GATEWAY_TOKEN_FILE|OPENCLAW_MODEL)=' "$bridge_env"
printf '%s\n' "unit check:"
systemctl --user cat "$HERMES_BRIDGE_SERVICE" | sed -n '1,80p'
printf '%s\n' "active-state: $(systemctl --user is-active "$HERMES_BRIDGE_SERVICE" 2>/dev/null || true)"
printf '%s\n' "enabled-state: $(systemctl --user is-enabled "$HERMES_BRIDGE_SERVICE" 2>/dev/null || true)"
REMOTE
