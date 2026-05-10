#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="ubuntu@52.79.96.56"
KEY_FILE="/Users/jiseong/git/PaperReviewAgent/jiseong.pem"
REMOTE_WORKSPACE='~/.openclaw/workspace'
SSH_CMD="ssh -i ${KEY_FILE}"

cd "$ROOT_DIR"

python3 scripts/check-prompt-governance.py
python3 scripts/check-runtime-manifests.py

${SSH_CMD} "$REMOTE_HOST" "mkdir -p $REMOTE_WORKSPACE/skills"

COPYFILE_DISABLE=1 rsync -az \
  -e "$SSH_CMD" \
  workspace/AGENTS.md \
  workspace/IDENTITY.md \
  workspace/SOUL.md \
  workspace/TOOLS.md \
  workspace/USER.md \
  workspace/MEMORY.md \
  workspace/HEARTBEAT.md \
  workspace/PROMPT_GOVERNANCE.md \
  workspace/PROMPT_REGISTRY.json \
  "$REMOTE_HOST:$REMOTE_WORKSPACE/"

COPYFILE_DISABLE=1 rsync -az --delete \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  -e "$SSH_CMD" \
  skills/ \
  "$REMOTE_HOST:$REMOTE_WORKSPACE/skills/"

${SSH_CMD} "$REMOTE_HOST" "find $REMOTE_WORKSPACE -name '._*' -delete; find $REMOTE_WORKSPACE/skills -name '*.sh' -exec chmod +x {} +"
${SSH_CMD} "$REMOTE_HOST" "export PATH=\$HOME/.npm-global/bin:\$PATH; openclaw agents set-identity --workspace ~/.openclaw/workspace --from-identity >/dev/null 2>&1 || true"

echo "Deployed workspace files to $REMOTE_HOST:$REMOTE_WORKSPACE"
