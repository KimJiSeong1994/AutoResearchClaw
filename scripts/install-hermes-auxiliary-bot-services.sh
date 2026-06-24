#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
HERMES_WORKSPACE="${HERMES_WORKSPACE:-~/.hermes/workspace}"
HERMES_AUX_CUTOVER="${HERMES_AUX_CUTOVER:-0}"
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
case "$HERMES_AUX_CUTOVER" in 0|1) ;; *) echo "FAIL: HERMES_AUX_CUTOVER must be 0 or 1" >&2; exit 1 ;; esac
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

echo "== install Hermes auxiliary bot services =="
echo "host: $REMOTE_HOST"
echo "cutover: $HERMES_AUX_CUTOVER"
echo

ssh "${SSH_OPTS[@]}" "$REMOTE_HOST" \
  "HERMES_WORKSPACE=$(quote_remote "$HERMES_WORKSPACE") HERMES_AUX_CUTOVER=$(quote_remote "$HERMES_AUX_CUTOVER") bash -s" <<'REMOTE'
set -euo pipefail
workspace="${HERMES_WORKSPACE/#\~/$HOME}"
project="$workspace/skills/discord-openclaw-bridge/project"
envf="$project/.env"
service_dir="$HOME/.config/systemd/user"
[ -d "$project" ] || { echo "FAIL: Hermes bridge project missing" >&2; exit 1; }
[ -f "$envf" ] || { echo "FAIL: Hermes bridge .env missing" >&2; exit 1; }
for entry in discord-jiphyeonjeon-miner discord-jiphyeonjeon-traveler discord-jiphyeonjeon-reporter; do
  [ -x "$project/.venv/bin/$entry" ] || { echo "FAIL: missing Hermes entrypoint $entry" >&2; exit 1; }
done
mkdir -p "$service_dir" "$HOME/.hermes/state" "$HOME/.hermes/workspace/blog-drafts"
install_unit() {
  local unit="$1" desc="$2" entry="$3" extra_rw="$4"
  cat > "$service_dir/$unit" <<SERVICE
[Unit]
Description=$desc
After=network-online.target hermes-gateway.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$project
EnvironmentFile=$envf
ExecStart=$project/.venv/bin/$entry
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$project $workspace $HOME/.hermes/state $extra_rw

[Install]
WantedBy=default.target
SERVICE
}
install_unit discord-hermes-jiphyeonjeon-miner.service "Discord bot for Jiphyeonjeon-Miner via Hermes" discord-jiphyeonjeon-miner ""
install_unit discord-hermes-jiphyeonjeon-traveler.service "Discord bot for Jiphyeonjeon-Traveler via Hermes" discord-jiphyeonjeon-traveler ""
install_unit discord-hermes-jiphyeonjeon-reporter.service "Discord app for Jiphyeonjeon-Reporter via Hermes" discord-jiphyeonjeon-reporter "$HOME/.hermes/workspace/blog-drafts"
systemctl --user daemon-reload
cutover_one() {
  local old="$1" new="$2" label="$3" pattern="$4"
  echo "--- cutover $label ---"
  local marker
  marker=$(date -u +%Y-%m-%d\ %H:%M:%S)
  systemctl --user stop "$old"
  sleep 5
  if [ "$(systemctl --user is-active "$old" 2>/dev/null || true)" = "active" ]; then
    echo "FAIL: $old did not stop" >&2
    return 1
  fi
  if ! systemctl --user start "$new"; then
    systemctl --user start "$old" || true
    return 1
  fi
  sleep 45
  if [ "$(systemctl --user is-active "$new" 2>/dev/null || true)" != "active" ]; then
    systemctl --user stop "$new" || true
    systemctl --user start "$old" || true
    return 1
  fi
  if ! journalctl --user -u "$new" --since "$marker UTC" --no-pager | grep -E "$pattern" >/dev/null; then
    journalctl --user -u "$new" --since "$marker UTC" --no-pager -n 200 || true
    systemctl --user stop "$new" || true
    systemctl --user start "$old" || true
    return 1
  fi
  if journalctl --user -u "$new" --since "$marker UTC" --no-pager | grep -Ei "traceback|exception|login failure|improper token|401|403|failed" >/dev/null; then
    journalctl --user -u "$new" --since "$marker UTC" --no-pager -n 200 || true
    systemctl --user stop "$new" || true
    systemctl --user start "$old" || true
    return 1
  fi
  systemctl --user disable "$old" >/dev/null 2>&1 || true
  systemctl --user enable "$new" >/dev/null
  echo "$label: cutover-ok"
}
if [ "$HERMES_AUX_CUTOVER" = "1" ]; then
  cutover_one discord-jiphyeonjeon-miner.service discord-hermes-jiphyeonjeon-miner.service miner "ready user=|synced .*miner|connected to Gateway"
  cutover_one discord-jiphyeonjeon-traveler.service discord-hermes-jiphyeonjeon-traveler.service traveler "ready user=|synced .*traveler|connected to Gateway"
  cutover_one discord-jiphyeonjeon-reporter.service discord-hermes-jiphyeonjeon-reporter.service reporter "ready user=|synced .*reporter|connected to Gateway"
fi
printf '%s\n' "--- auxiliary states ---"
for u in discord-jiphyeonjeon-miner.service discord-hermes-jiphyeonjeon-miner.service discord-jiphyeonjeon-traveler.service discord-hermes-jiphyeonjeon-traveler.service discord-jiphyeonjeon-reporter.service discord-hermes-jiphyeonjeon-reporter.service; do
  printf "%s: active=" "$u"; systemctl --user is-active "$u" 2>/dev/null || true
  printf "%s: enabled=" "$u"; systemctl --user is-enabled "$u" 2>/dev/null || true
done
REMOTE
