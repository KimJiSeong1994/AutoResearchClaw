#!/usr/bin/env bash
# Run the miner seed expansion pipeline and rotate logs older than 14 days.
#
# Called by the JIPHYEONJEON MINER SEEDS cron job (installed via
# install-miner-seeds-cron.sh).  Can also be invoked manually:
#
#   bash skills/discord-openclaw-bridge/project/scripts/run-miner-seeds.sh
#   MINER_SEEDS_DRY_RUN=1 bash skills/discord-openclaw-bridge/project/scripts/run-miner-seeds.sh
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${HOME}/.openclaw/workspace"
LOG_DIR="${WORKSPACE}/logs"
LOG_FILE="${LOG_DIR}/miner-seeds.log"

mkdir -p "${LOG_DIR}"

# Rotate: delete log entries older than 14 days
find "${LOG_DIR}" -name "miner-seeds*.log" -mtime +14 -delete 2>/dev/null || true

exec >>"${LOG_FILE}" 2>&1

printf "\n[%s] miner-seeds start\n" "$(date -Is)"

cd "${PROJECT_DIR}"

if [ "${MINER_SEEDS_DRY_RUN:-0}" = "1" ]; then
    echo "dry-run: would run .venv/bin/discord-openclaw-miner-seeds"
    .venv/bin/discord-openclaw-miner-seeds --dry-run
    printf "[%s] miner-seeds dry-run complete\n" "$(date -Is)"
    exit 0
fi

CLI_EXIT=0
.venv/bin/discord-openclaw-miner-seeds || CLI_EXIT=$?
printf "[%s] miner-seeds done (exit=%s)\n" "$(date -Is)" "${CLI_EXIT}"

if [ "${MINER_SEEDS_SKIP_DISCORD_REPORT:-0}" != "1" ]; then
    .venv/bin/discord-openclaw-post-miner-seeds-report || \
        printf "[%s] WARN miner-seeds discord report failed (continuing)\n" "$(date -Is)"
fi

exit "${CLI_EXIT}"
