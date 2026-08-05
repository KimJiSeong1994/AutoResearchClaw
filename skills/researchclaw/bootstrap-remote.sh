#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/.hermes/workspace/projects/AutoResearchClaw}"
CONFIG_FILE="${PROJECT_DIR}/config.yaml"

mkdir -p "$HOME/.hermes/workspace/projects"

if [ ! -x "$HOME/.local/bin/uv" ] && ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

[ -s "$HOME/.hermes_gateway_token" ] || {
  echo "Hermes gateway token file is missing" >&2
  exit 1
}

uv python install 3.11

if [ ! -d "${PROJECT_DIR}/.git" ]; then
  git clone --depth 1 https://github.com/aiming-lab/AutoResearchClaw.git "$PROJECT_DIR"
else
  git -C "$PROJECT_DIR" fetch --depth 1 origin main
  git -C "$PROJECT_DIR" reset --hard origin/main
fi

cd "$PROJECT_DIR"
uv venv --python 3.11 --allow-existing .venv
uv pip install -e .
cp -n config.researchclaw.example.yaml config.yaml

python3 - <<'PY'
import yaml
from pathlib import Path
p = Path.home()/".hermes"/"workspace"/"projects"/"AutoResearchClaw"/"config.yaml"
cfg = yaml.safe_load(p.read_text())
cfg["runtime"]["timezone"] = "Asia/Seoul"
cfg["llm"]["provider"] = "openai-compatible"
cfg["llm"]["base_url"] = "http://127.0.0.1:28789/v1"
cfg["llm"]["wire_api"] = "chat_completions"
cfg["llm"]["api_key_env"] = "HERMES_GATEWAY_TOKEN"
cfg["llm"]["api_key"] = ""
cfg["llm"]["primary_model"] = "hermes-agent"
cfg["llm"]["fallback_models"] = []
cfg["experiment"]["mode"] = "sandbox"
cfg["experiment"]["sandbox"]["python_path"] = ".venv/bin/python"
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
PY

cat > "$PROJECT_DIR/.env.hermes" <<'EOF'
export HERMES_GATEWAY_TOKEN="$(tr -d '\n' < "$HOME/.hermes_gateway_token")"
EOF

cat > "$PROJECT_DIR/RESEARCHCLAW_AGENTS.md" <<'EOF'
# RESEARCHCLAW_AGENTS.md

This repository is pre-wired to run through the local Hermes gateway on the same host.

## Quick start

```bash
cd ~/.hermes/workspace/projects/AutoResearchClaw
source .venv/bin/activate
source .env.hermes
researchclaw validate --config config.yaml
researchclaw run --config config.yaml --topic "Your research topic" --auto-approve
```

## Integration facts

- Gateway base URL: `http://127.0.0.1:28789/v1`
- Gateway model target: `hermes-agent`
- Auth env var: `HERMES_GATEWAY_TOKEN`
- Python runtime: `.venv/bin/python` (Python 3.11)

## Operator notes

- Keep the Hermes gateway on loopback only.
- Prefer `researchclaw validate` after config edits.
- Outputs land under `artifacts/`.
- This setup uses the Hermes OpenAI-compatible `/v1` API, not direct provider keys.
EOF

echo "Bootstrap complete: $PROJECT_DIR"
