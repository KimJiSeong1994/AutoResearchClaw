#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

JIPHY_TOKEN="${JIPHYEONJEON_TOKEN:-}"
if [ -z "$JIPHY_TOKEN" ]; then
  JIPHY_TOKEN="$(python3 -c "
import json, sys
try:
    d = json.load(open('/Users/jiseong/.claude/.claude.json'))
    m = d.get('mcpServers', {}).get('jiphyeonjeon', {})
    tok = m.get('env', {}).get('JIPHYEONJEON_TOKEN', '')
    sys.stdout.write(tok)
except Exception:
    pass
")"
fi

if [ -z "$JIPHY_TOKEN" ]; then
  echo "ERROR: JIPHYEONJEON_TOKEN not provided and not found in .claude.json" >&2
  exit 1
fi

bash "$SKILL_DIR/deploy.sh"

# Phase 1: non-secret setup (token NOT in this heredoc or command line).
ssh -i "$KEY_FILE" "$REMOTE_HOST" "REMOTE_PROJECT=$REMOTE_PROJECT bash -s" <<'REMOTE'
set -euo pipefail
PROJECT_DIR="${REMOTE_PROJECT/#\~/$HOME}"
cd "$PROJECT_DIR"

if [ ! -x "$HOME/.local/bin/uv" ] && ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

uv python install 3.11
uv venv --python 3.11 --allow-existing .venv
uv pip install -e .

if [ ! -f "$PROJECT_DIR/config.yaml" ]; then
  cp config.example.yaml config.yaml
fi

mkdir -p "$PROJECT_DIR/state" "$PROJECT_DIR/artifacts" "$PROJECT_DIR/logs"

if [ ! -f "$HOME/.openclaw_gateway_token" ]; then
  python3 - <<PY
import json
from pathlib import Path
p = Path.home()/".openclaw"/"openclaw.json"
if p.exists():
    data = json.loads(p.read_text())
    tok = data.get("gateway", {}).get("auth", {}).get("token", "")
    if tok:
        (Path.home()/".openclaw_gateway_token").write_text(tok + "\n")
PY
fi

echo "phase-1 setup complete: $PROJECT_DIR"
REMOTE

# Phase 2: deliver JWT via stdin — never appears on any command line or in `ps`.
# Bash receives stdin from the local pipe, so the remote script must be passed
# as the SSH command argument (NOT via heredoc — the pipe wins over heredoc and
# would silently make the token become the script body).
PHASE2_SCRIPT='set -euo pipefail
PROJECT_DIR="$HOME/.openclaw/workspace/projects/paper-recommender"
mkdir -p "$PROJECT_DIR"
umask 077
tok="$(cat)"
env_file="$PROJECT_DIR/.env"
tmp="$(mktemp "$PROJECT_DIR/.env.XXXXXX")"
printf "JIPHYEONJEON_TOKEN=%s\n" "$tok" > "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$env_file"
echo "phase-2: .env written ($(wc -c < "$env_file") bytes)"
'
printf '%s' "$JIPHY_TOKEN" | ssh -i "$KEY_FILE" "$REMOTE_HOST" "$PHASE2_SCRIPT"

echo "Done. Try: bash $SKILL_DIR/doctor.sh"
