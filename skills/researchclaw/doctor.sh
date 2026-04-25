#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/.openclaw/workspace/projects/AutoResearchClaw}"
cd "$PROJECT_DIR"
export OPENCLAW_GATEWAY_TOKEN="$(tr -d '\n' < "$HOME/.openclaw_gateway_token")"
.venv/bin/researchclaw doctor --config config.yaml
