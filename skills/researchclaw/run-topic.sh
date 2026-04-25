#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 \"research topic\"" >&2
  exit 1
fi

PROJECT_DIR="${PROJECT_DIR:-$HOME/.openclaw/workspace/projects/AutoResearchClaw}"
cd "$PROJECT_DIR"
export OPENCLAW_GATEWAY_TOKEN="$(tr -d '\n' < "$HOME/.openclaw_gateway_token")"
.venv/bin/researchclaw run --config config.yaml --topic "$*" --auto-approve
