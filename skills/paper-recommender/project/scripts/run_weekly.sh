#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m paper_recommender --config config.yaml weekly-report "$@"
