#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-~/.openclaw/workspace}"
SSH_OPTS=(
  -i "$KEY_FILE"
  -o BatchMode=yes
  -o ConnectTimeout="${SSH_CONNECT_TIMEOUT:-10}"
  -o ServerAliveInterval="${SSH_SERVER_ALIVE_INTERVAL:-15}"
  -o ServerAliveCountMax="${SSH_SERVER_ALIVE_COUNT_MAX:-2}"
)

echo "== remote ops readiness =="
echo "host: $REMOTE_HOST"
echo

ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" "REMOTE_WORKSPACE=$REMOTE_WORKSPACE bash -s" <<'REMOTE'
set -euo pipefail

workspace="${REMOTE_WORKSPACE/#\~/$HOME}"
failures=0
warnings=0

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

export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:$PATH"
export NODE_COMPILE_CACHE="${NODE_COMPILE_CACHE:-/var/tmp/openclaw-compile-cache}"
export OPENCLAW_NO_RESPAWN=1

section "workspace"
echo "$workspace"
[ -d "$workspace" ] || mark_fail "workspace directory missing"

section "openclaw service"
if systemctl --user list-unit-files openclaw-gateway.service >/dev/null 2>&1; then
  systemctl --user is-active --quiet openclaw-gateway.service \
    && echo "openclaw-gateway.service: active" \
    || mark_fail "openclaw-gateway.service is not active"
  systemctl --user --no-pager --lines=0 status openclaw-gateway.service 2>/dev/null | sed -n '1,8p' || true
else
  mark_warn "openclaw-gateway.service is not installed as a user unit"
fi

section "openclaw gateway"
if command -v openclaw >/dev/null 2>&1; then
  openclaw gateway status || mark_fail "openclaw gateway status failed"
else
  mark_fail "openclaw CLI not found on PATH"
fi

section "listeners"
if command -v ss >/dev/null 2>&1; then
  ss -ltnp | grep -E '(:18789|:18791)\b' || mark_fail "expected loopback listeners 18789/18791 not found"
else
  mark_warn "ss command unavailable; listener check skipped"
fi

section "loopback /v1 probe"
token_file="${OPENCLAW_GATEWAY_TOKEN_FILE:-$HOME/.openclaw_gateway_token}"
if command -v curl >/dev/null 2>&1 && [ -f "$token_file" ]; then
  curl -fsS --max-time 15 -H "Authorization: Bearer $(tr -d '\n' < "$token_file")" \
    "http://127.0.0.1:18789/v1/models" >/dev/null \
    && echo "models endpoint: ok" \
    || mark_fail "models endpoint probe failed"
else
  mark_warn "curl or gateway token file missing; /v1 probe skipped"
fi

section "discord bridge"
if systemctl --user list-unit-files discord-openclaw-bridge.service >/dev/null 2>&1; then
  systemctl --user is-active --quiet discord-openclaw-bridge.service \
    && echo "discord-openclaw-bridge.service: active" \
    || mark_warn "discord-openclaw-bridge.service is not active"
  systemctl --user --no-pager --lines=0 status discord-openclaw-bridge.service 2>/dev/null | sed -n '1,8p' || true
else
  mark_warn "discord-openclaw-bridge.service is not installed"
fi

section "jiphyeonjeon guard"
guard_project="$workspace/skills/discord-openclaw-bridge/project"
guard_status="$workspace/state/miner-seeds-last-status.json"
guard_queue="$workspace/review/jiphyeonjeon-claw/link-review-queue.jsonl"
guard_decisions="$workspace/review/jiphyeonjeon-claw/link-review-decisions.jsonl"
guard_log="$workspace/logs/miner-seeds.log"
if [ -d "$guard_project" ]; then
  echo "$guard_project"
  if [ -x "$guard_project/.venv/bin/discord-openclaw-guard-ops-digest" ]; then
    "$guard_project/.venv/bin/discord-openclaw-guard-ops-digest" \
      --status-path "$guard_status" \
      --review-queue-path "$guard_queue" \
      --decisions-path "$guard_decisions" \
      --env-path "$guard_project/.env" \
      --fail-on-error \
      || mark_warn "jiphyeonjeon guard ops digest failed"
  else
    mark_warn "guard ops digest CLI missing under discord bridge .venv"
  fi
  if [ -f "$guard_project/.env" ]; then
    grep -q '^DISCORD_GUARD_BOT_TOKEN=.' "$guard_project/.env" \
      && echo "DISCORD_GUARD_BOT_TOKEN: set" \
      || mark_warn "DISCORD_GUARD_BOT_TOKEN missing; reports may use bridge fallback identity"
    grep -q '^DISCORD_OPS_REPORT_CHANNEL_ID=.' "$guard_project/.env" \
      && echo "DISCORD_OPS_REPORT_CHANNEL_ID: set" \
      || echo "DISCORD_OPS_REPORT_CHANNEL_ID: default"
  else
    mark_warn "discord bridge .env missing; guard config cannot be checked"
  fi
  [ -f "$guard_log" ] && tail -n 20 "$guard_log" | grep -E 'WARN|ERROR|failed|exit=[1-9]' || true
else
  mark_warn "discord bridge project missing; guard digest skipped"
fi

section "researchclaw project"
research_project="$workspace/projects/AutoResearchClaw"
if [ -d "$research_project" ]; then
  echo "$research_project"
  [ -x "$research_project/.venv/bin/researchclaw" ] \
    && echo "researchclaw CLI: present" \
    || mark_warn "researchclaw CLI missing under .venv"
  [ -f "$research_project/config.yaml" ] \
    && echo "config.yaml: present" \
    || mark_warn "researchclaw config.yaml missing"
else
  mark_warn "ResearchClaw project missing: $research_project"
fi

section "paper recommender last run"
paper_status="$workspace/projects/paper-recommender/state/last_run_status.json"
if [ -f "$paper_status" ]; then
  if ! PAPER_STATUS="$paper_status" python3 - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

path = Path(os.environ["PAPER_STATUS"])
data = json.loads(path.read_text())
ts = data.get("timestamp")
print(f"timestamp: {ts}")
print(f"candidates: {data.get('candidate_count')}")
print(f"clusters: {data.get('cluster_count')}")
print(f"deep ok: {data.get('deep_success_count')}/{data.get('deep_attempted')}")
if ts:
    parsed = dt.datetime.fromisoformat(ts)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    age_h = (dt.datetime.now(dt.timezone.utc) - parsed).total_seconds() / 3600
    print(f"age_hours: {age_h:.1f}")
    if age_h > 25:
        raise SystemExit("last paper recommender run is stale (>25h)")
PY
  then
    mark_warn "paper recommender last_run_status.json is stale or unreadable"
  fi
else
  mark_warn "paper recommender last_run_status.json missing"
fi

section "recent openclaw log signal"
latest_log="$(ls -1t /tmp/openclaw/*.log 2>/dev/null | head -1 || true)"
if [ -n "$latest_log" ]; then
  echo "$latest_log"
  LOG_PATH="$latest_log" python3 - <<'PY'
import json
import os
from collections import deque
from pathlib import Path

path = Path(os.environ["LOG_PATH"])
lines = deque(path.open(errors="replace"), maxlen=120)
matched = []
for line in lines:
    raw = line.rstrip("\n")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        lower = raw.lower()
        if any(word in lower for word in ("error", "warn", "fail", "exception", "timeout")):
            matched.append(raw[:240])
        continue
    meta = payload.get("_meta", {}) if isinstance(payload, dict) else {}
    level = str(meta.get("logLevelName", "")).upper()
    if level not in {"WARN", "ERROR"}:
        continue
    parts = []
    for key in ("0", "1"):
        value = payload.get(key)
        if value:
            parts.append(str(value).replace("\n", " "))
    message = " | ".join(parts) or raw
    matched.append(f"{level}: {message[:220]}")

if matched:
    print("\n".join(matched[-20:]))
else:
    print("no recent warn/error lines")
PY
else
  mark_warn "no runtime log found under /tmp/openclaw"
fi

section "summary"
echo "warnings: $warnings"
echo "failures: $failures"
[ "$failures" -eq 0 ]
REMOTE
