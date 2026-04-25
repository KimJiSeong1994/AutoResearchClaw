#!/usr/bin/env bash
set -euo pipefail

workspace="${HOME}/.openclaw/workspace"

echo "== workspace root =="
printf '%s\n' "$workspace"
echo
echo "== top-level files =="
find "$workspace" -maxdepth 1 -type f | sort
echo
echo "== skills =="
find "$workspace/skills" -maxdepth 2 -name 'SKILL.md' | sort || true
