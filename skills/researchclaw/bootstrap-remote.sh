#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/.openclaw/workspace/projects/AutoResearchClaw}"
CONFIG_FILE="${PROJECT_DIR}/config.yaml"

mkdir -p "$HOME/.openclaw/workspace/projects"

if [ ! -x "$HOME/.local/bin/uv" ] && ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

python3 - <<'PY'
import json
from pathlib import Path
p = Path.home()/".openclaw"/"openclaw.json"
data = json.loads(p.read_text())
data.setdefault("gateway", {}).setdefault("http", {}).setdefault("endpoints", {}).setdefault("chatCompletions", {})["enabled"] = True
token = data["gateway"]["auth"]["token"]
(Path.home()/".openclaw_gateway_token").write_text(token + "\n")
p.write_text(json.dumps(data, indent=2) + "\n")
PY

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
p = Path.home()/".openclaw"/"workspace"/"projects"/"AutoResearchClaw"/"config.yaml"
cfg = yaml.safe_load(p.read_text())
cfg["runtime"]["timezone"] = "Asia/Seoul"
cfg["llm"]["provider"] = "openai-compatible"
cfg["llm"]["base_url"] = "http://127.0.0.1:18789/v1"
cfg["llm"]["wire_api"] = "chat_completions"
cfg["llm"]["api_key_env"] = "OPENCLAW_GATEWAY_TOKEN"
cfg["llm"]["api_key"] = ""
cfg["llm"]["primary_model"] = "openclaw/clawbridge"
cfg["llm"]["fallback_models"] = ["openclaw/default"]
cfg["experiment"]["mode"] = "sandbox"
cfg["experiment"]["sandbox"]["python_path"] = ".venv/bin/python"
cfg["openclaw_bridge"] = {
    "use_cron": False,
    "use_message": False,
    "use_memory": False,
    "use_sessions_spawn": False,
    "use_web_fetch": False,
    "use_browser": False,
}
p.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
PY

cat > "$PROJECT_DIR/.env.openclaw" <<'EOF'
export OPENCLAW_GATEWAY_TOKEN="$(tr -d '\n' < "$HOME/.openclaw_gateway_token")"
EOF

cat > "$PROJECT_DIR/RESEARCHCLAW_AGENTS.md" <<'EOF'
# RESEARCHCLAW_AGENTS.md

This repository is pre-wired to run through the local OpenClaw gateway on the same host.

## Quick start

```bash
cd ~/.openclaw/workspace/projects/AutoResearchClaw
source .venv/bin/activate
source .env.openclaw
researchclaw validate --config config.yaml
researchclaw run --config config.yaml --topic "Your research topic" --auto-approve
```

## Integration facts

- Gateway base URL: `http://127.0.0.1:18789/v1`
- Gateway model target: `openclaw/clawbridge`
- Auth env var: `OPENCLAW_GATEWAY_TOKEN`
- Python runtime: `.venv/bin/python` (Python 3.11)

## Operator notes

- Keep the OpenClaw gateway on loopback only.
- Prefer `researchclaw validate` after config edits.
- Outputs land under `artifacts/`.
- This setup uses the OpenClaw OpenAI-compatible `/v1` API, not direct OpenAI keys.
EOF

export PATH="$HOME/.npm-global/bin:$PATH"
openclaw gateway restart >/dev/null
sleep 2

echo "Bootstrap complete: $PROJECT_DIR"
