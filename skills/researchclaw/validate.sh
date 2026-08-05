#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/.hermes/workspace/projects/AutoResearchClaw}"
cd "$PROJECT_DIR"
export HERMES_GATEWAY_TOKEN="$(tr -d '\n' < "$HOME/.hermes_gateway_token")"
.venv/bin/researchclaw validate --config config.yaml
