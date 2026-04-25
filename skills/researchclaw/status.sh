#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/.openclaw/workspace/projects/AutoResearchClaw}"

echo "== project =="
printf '%s\n' "$PROJECT_DIR"
echo
echo "== versions =="
"$HOME/.local/bin/uv" --version
"$PROJECT_DIR/.venv/bin/python" --version
"$PROJECT_DIR/.venv/bin/researchclaw" --help >/dev/null
"$PROJECT_DIR/.venv/bin/python" -c 'import researchclaw; print("researchclaw import ok")'
echo
echo "== git =="
git -C "$PROJECT_DIR" rev-parse --short HEAD
echo
echo "== config llm =="
python3 - <<'PY'
import yaml
from pathlib import Path
p = Path.home()/".openclaw"/"workspace"/"projects"/"AutoResearchClaw"/"config.yaml"
cfg = yaml.safe_load(p.read_text())
print("base_url:", cfg["llm"]["base_url"])
print("primary_model:", cfg["llm"]["primary_model"])
print("fallback_models:", cfg["llm"].get("fallback_models"))
print("sandbox_python:", cfg["experiment"]["sandbox"]["python_path"])
PY
