#!/usr/bin/env bash
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"

ssh -i "$KEY_FILE" "$REMOTE_HOST" "REMOTE_PROJECT=$REMOTE_PROJECT bash -s" <<'REMOTE'
set -euo pipefail
PROJECT_DIR="${REMOTE_PROJECT/#\~/$HOME}"

echo "== project =="
echo "$PROJECT_DIR"
echo

echo "== versions =="
if [ -x "$HOME/.local/bin/uv" ]; then "$HOME/.local/bin/uv" --version; fi
"$PROJECT_DIR/.venv/bin/python" --version 2>/dev/null || echo "venv missing"
"$PROJECT_DIR/.venv/bin/paper-recommender" --help >/dev/null 2>&1 \
  && echo "paper-recommender CLI ok" \
  || echo "paper-recommender CLI FAIL"
echo

echo "== config (llm) =="
python3 - <<PY
import yaml
from pathlib import Path
p = Path("$PROJECT_DIR") / "config.yaml"
if p.exists():
    cfg = yaml.safe_load(p.read_text())
    print("openclaw.base_url:", cfg["openclaw"]["base_url"])
    print("openclaw.primary_model:", cfg["openclaw"]["primary_model"])
    print("rerank.top_k:", cfg["rerank"]["top_k"])
else:
    print("config.yaml missing")
PY
echo

echo "== artifacts (latest 5) =="
ls -1t "$PROJECT_DIR/artifacts" 2>/dev/null | head -5 || echo "none"
echo

echo "== last run log =="
LATEST_LOG="$(ls -1t "$PROJECT_DIR/logs"/run_*.log 2>/dev/null | head -1 || true)"
if [ -n "$LATEST_LOG" ]; then
  echo "$LATEST_LOG"
  tail -n 15 "$LATEST_LOG"
else
  echo "no logs yet"
fi

echo
echo "== cron =="
crontab -l 2>/dev/null | grep -E "paper-recommender|run_daily.sh" || echo "no cron entry"
REMOTE
