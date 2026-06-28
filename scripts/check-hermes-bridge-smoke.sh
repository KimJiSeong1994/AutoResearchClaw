#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
HERMES_WORKSPACE="${HERMES_WORKSPACE:-~/.hermes/workspace}"
HERMES_BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}"
HERMES_TOKEN_FILE="${HERMES_GATEWAY_TOKEN_FILE:-~/.hermes_gateway_token}"
HERMES_MODEL="${HERMES_MODEL:-hermes-agent-canary}"
HERMES_SMOKE_EXPECTED="${HERMES_SMOKE_EXPECTED:-BRIDGE_HERMES_SMOKE_OK}"
HERMES_SMOKE_TIMEOUT_SEC="${HERMES_SMOKE_TIMEOUT_SEC:-600}"
case "$HERMES_WORKSPACE" in
  "~/.hermes/"*|"~/.hermes") ;;
  *)
    echo "FAIL: HERMES_WORKSPACE must stay under the ~/.hermes canary directory" >&2
    exit 1
    ;;
esac
case "$HERMES_WORKSPACE" in
  *"'"*)
    echo "FAIL: HERMES_WORKSPACE contains unsafe shell characters" >&2
    exit 1
    ;;
esac
case "$HERMES_WORKSPACE" in
  *"/../"*|*"../"*|*".."|*"/..")
    echo "FAIL: HERMES_WORKSPACE must not contain parent-directory traversal" >&2
    exit 1
    ;;
esac
if [[ ! "$HERMES_BASE_URL" =~ ^http://(127\.0\.0\.1|localhost):[0-9]+(/.*)?$ ]]; then
  echo "FAIL: HERMES_BASE_URL must remain strict loopback http://127.0.0.1:<port>/... or http://localhost:<port>/..." >&2
  exit 1
fi
case "$HERMES_SMOKE_EXPECTED" in
  *[!A-Za-z0-9_:-]*)
    echo "FAIL: HERMES_SMOKE_EXPECTED contains unsafe characters" >&2
    exit 1
    ;;
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

quote_remote() {
  printf '%q' "$1"
}

echo "== remote Hermes bridge smoke =="
echo "host: $REMOTE_HOST"
echo

ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" \
  "HERMES_WORKSPACE=$(quote_remote "$HERMES_WORKSPACE") HERMES_BASE_URL=$(quote_remote "$HERMES_BASE_URL") HERMES_TOKEN_FILE=$(quote_remote "$HERMES_TOKEN_FILE") HERMES_MODEL=$(quote_remote "$HERMES_MODEL") HERMES_SMOKE_EXPECTED=$(quote_remote "$HERMES_SMOKE_EXPECTED") HERMES_SMOKE_TIMEOUT_SEC=$(quote_remote "$HERMES_SMOKE_TIMEOUT_SEC") bash -s" <<'REMOTE'
set -euo pipefail

workspace="${HERMES_WORKSPACE/#\~/$HOME}"
base_url="${HERMES_BASE_URL%/}"
token_file="${HERMES_TOKEN_FILE/#\~/$HOME}"
project_dir="$workspace/skills/discord-openclaw-bridge/project"

section() {
  printf '\n== %s ==\n' "$1"
}

section "workspace"
echo "$project_dir"
[ -d "$project_dir" ] || { echo "FAIL: canary bridge project missing" >&2; exit 1; }
[ -f "$token_file" ] || { echo "FAIL: Hermes gateway token file missing" >&2; exit 1; }

section "python runtime"
if [ -x "$project_dir/.venv/bin/python" ]; then
  python_bin="$project_dir/.venv/bin/python"
elif [ -x "$HOME/.hermes/hermes-agent/venv/bin/python" ]; then
  python_bin="$HOME/.hermes/hermes-agent/venv/bin/python"
else
  python_bin="python3"
fi
echo "$python_bin"

section "bridge client smoke"
cd "$project_dir"
export HERMES_BASE_URL="$base_url"
export HERMES_GATEWAY_TOKEN_FILE="$token_file"
export HERMES_MODEL
export HERMES_SMOKE_EXPECTED
export HERMES_SMOKE_TIMEOUT_SEC
export PYTHONPATH="$project_dir/src${PYTHONPATH:+:$PYTHONPATH}"
"$python_bin" - <<'PY'
import asyncio
import os
from pathlib import Path

from discord_openclaw_bridge.openclaw_gateway import OpenClawGatewayClient, OpenClawGatewayPolicy

async def main() -> None:
    token = Path(os.environ["HERMES_GATEWAY_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    expected = os.environ["HERMES_SMOKE_EXPECTED"]
    policy = OpenClawGatewayPolicy.from_values(
        base_url=os.environ["HERMES_BASE_URL"],
        token=token,
        primary_model=os.environ["HERMES_MODEL"],
        timeout_sec=float(os.environ["HERMES_SMOKE_TIMEOUT_SEC"]),
        user_agent="discord-openclaw-bridge-hermes-smoke/1.0",
    )
    async with OpenClawGatewayClient(policy) as client:
        await client.models_health()
        text = await client.chat_completion(
            os.environ["HERMES_MODEL"],
            [{"role": "user", "content": f"Reply with exactly {expected} and nothing else."}],
            temperature=0,
            max_tokens=32,
        )
    if expected not in text:
        print("FAIL: unexpected bridge smoke response")
        raise SystemExit(2)
    print("bridge chat smoke: ok")

asyncio.run(main())
PY
REMOTE
