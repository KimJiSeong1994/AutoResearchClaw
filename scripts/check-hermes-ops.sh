#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
HERMES_WORKSPACE="${HERMES_WORKSPACE:-~/.hermes/workspace}"
HERMES_BASE_URL="${HERMES_BASE_URL:-http://127.0.0.1:28789/v1}"
HERMES_TOKEN_FILE="${HERMES_GATEWAY_TOKEN_FILE:-~/.hermes_gateway_token}"
HERMES_SERVICE="${HERMES_SERVICE:-hermes-gateway.service}"
HERMES_LOG_GLOB="${HERMES_LOG_GLOB:-/tmp/hermes/*.log}"
case "$HERMES_WORKSPACE" in
  ~/.hermes/*|~/.hermes|*/.hermes/*|*/.hermes)
    ;;
  *)
    echo "FAIL: HERMES_WORKSPACE must stay under a .hermes canary directory" >&2
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

case "$HERMES_BASE_URL" in
  http://127.0.0.1:*|http://localhost:*) ;;
  *) echo "FAIL: HERMES_BASE_URL must remain loopback for canary readiness" >&2; exit 1 ;;
esac

quote_remote() {
  printf '%q' "$1"
}

echo "== remote Hermes canary readiness =="
echo "host: $REMOTE_HOST"
echo

ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" \
  "HERMES_WORKSPACE=$(quote_remote "$HERMES_WORKSPACE") HERMES_BASE_URL=$(quote_remote "$HERMES_BASE_URL") HERMES_TOKEN_FILE=$(quote_remote "$HERMES_TOKEN_FILE") HERMES_SERVICE=$(quote_remote "$HERMES_SERVICE") HERMES_LOG_GLOB=$(quote_remote "$HERMES_LOG_GLOB") bash -s" <<'REMOTE'
set -euo pipefail

workspace="${HERMES_WORKSPACE/#\~/$HOME}"
base_url="${HERMES_BASE_URL%/}"
token_file="${HERMES_TOKEN_FILE/#\~/$HOME}"
failures=0
warnings=0
trap 'rm -f "${curl_config:-}"' EXIT

section() {
  printf '\n== %s ==\n' "$1"
}

mark_fail() {
  echo "FAIL: $*"
  failures=$((failures + 1))
}

mark_warn() {
  echo "WARN: $*"
  warnings=$((warnings + 1))
}

section "workspace"
echo "$workspace"
[ -d "$workspace" ] || mark_warn "Hermes canary workspace directory missing"

section "hermes service"
if systemctl --user list-unit-files "$HERMES_SERVICE" >/dev/null 2>&1; then
  systemctl --user is-active --quiet "$HERMES_SERVICE" \
    && echo "$HERMES_SERVICE: active" \
    || mark_fail "$HERMES_SERVICE is not active"
  systemctl --user --no-pager --lines=0 status "$HERMES_SERVICE" 2>/dev/null | sed -n '1,8p' || true
else
  mark_warn "$HERMES_SERVICE is not installed as a user unit"
fi

section "listeners"
if command -v ss >/dev/null 2>&1; then
  port="$(printf '%s\n' "$base_url" | sed -E 's#^http://(127\.0\.0\.1|localhost):([0-9]+).*#\2#')"
  if [ -n "$port" ] && [ "$port" != "$base_url" ]; then
    listener_addresses="$(ss -ltn | awk -v suffix=":$port" '$4 ~ suffix "$" {print $4}')"
    if [ -z "$listener_addresses" ]; then
      mark_warn "expected Hermes loopback listener on port $port not found"
    else
      echo "$listener_addresses"
      non_loopback="$(printf '%s\n' "$listener_addresses" | grep -Ev "(^127\\.0\\.0\\.1:${port}$|^localhost:${port}$|^\\[::1\\]:${port}$|^::1:${port}$)" || true)"
      [ -z "$non_loopback" ] || mark_fail "Hermes listener is not loopback-only: $(printf '%s' "$non_loopback" | tr '\n' ' ')"
    fi
  else
    mark_warn "could not parse Hermes loopback port from HERMES_BASE_URL"
  fi
else
  mark_warn "ss command unavailable; listener check skipped"
fi

section "loopback /v1 probe"
if command -v curl >/dev/null 2>&1 && [ -f "$token_file" ]; then
  curl_config="$(mktemp)"
  chmod 600 "$curl_config"
  {
    printf 'fail\n'
    printf 'silent\n'
    printf 'show-error\n'
    printf 'max-time = 15\n'
    printf 'header = "Authorization: Bearer '
    tr -d '\n' < "$token_file"
    printf '"\n'
  } > "$curl_config"
  if curl --config "$curl_config" "$base_url/models" >/dev/null; then
    echo "models endpoint: ok"
  else
    mark_fail "models endpoint probe failed"
  fi
  rm -f "$curl_config"
else
  mark_warn "curl or Hermes gateway token file missing; /v1 probe skipped"
fi

section "discord bridge canary env"
bridge_env="$workspace/skills/discord-openclaw-bridge/project/.env"
if [ -f "$bridge_env" ]; then
  grep -q '^HERMES_BASE_URL=.' "$bridge_env" \
    && echo "HERMES_BASE_URL: set" \
    || mark_warn "HERMES_BASE_URL not set in canary bridge .env"
  if grep -q '^HERMES_GATEWAY_TOKEN=.' "$bridge_env"; then
    echo "HERMES_GATEWAY_TOKEN: set"
  elif grep -q '^HERMES_GATEWAY_TOKEN_FILE=.' "$bridge_env"; then
    echo "HERMES_GATEWAY_TOKEN_FILE: set"
  else
    mark_warn "Hermes gateway token env is not set in canary bridge .env"
  fi
else
  mark_warn "canary bridge .env missing; Hermes env aliases cannot be checked"
fi

section "recent hermes log signal"
latest_log="$(ls -1t $HERMES_LOG_GLOB 2>/dev/null | head -1 || true)"
if [ -n "$latest_log" ]; then
  echo "$latest_log"
  LOG_PATH="$latest_log" python3 - <<'PY'
import os
from collections import deque
from pathlib import Path

path = Path(os.environ["LOG_PATH"])
lines = deque(path.open(errors="replace"), maxlen=120)
matched = []
for line in lines:
    raw = line.rstrip("\n")
    lower = raw.lower()
    if any(word in lower for word in ("error", "warn", "fail", "exception", "timeout")):
        matched.append(raw[:240])
if matched:
    print("\n".join(matched[-20:]))
else:
    print("no recent warn/error lines")
PY
else
  mark_warn "no Hermes runtime log found under configured glob"
fi

section "summary"
echo "warnings: $warnings"
echo "failures: $failures"
[ "$failures" -eq 0 ]
REMOTE
