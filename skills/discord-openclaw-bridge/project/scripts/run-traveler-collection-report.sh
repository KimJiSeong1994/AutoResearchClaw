#!/usr/bin/env bash
# Run the daily 집현전-여행자 additional-collection report.
# Intended cron schedule: 13:00 UTC = 22:00 Asia/Seoul (KST).
set -euo pipefail

export PATH="${HOME}/.local/bin:${HOME}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export TZ="${TZ:-Asia/Seoul}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../.venv" ]; then
  PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  PROJECT_DIR="${HOME}/.openclaw/workspace/skills/discord-openclaw-bridge/project"
fi
WORKSPACE="${HOME}/.openclaw/workspace"
LOG_DIR="${WORKSPACE}/logs"
LOG_FILE="${LOG_DIR}/traveler-collection-report.log"

mkdir -p "${LOG_DIR}"
find "${LOG_DIR}" -name "traveler-collection-report*.log" -mtime +14 -delete 2>/dev/null || true

exec >>"${LOG_FILE}" 2>&1
printf "\n[%s] traveler-collection-report start\n" "$(date -Is)"
cd "${PROJECT_DIR}"

if [ "${TRAVELER_COLLECTION_REPORT_DRY_RUN:-0}" = "1" ]; then
  echo "dry-run: running traveler source discovery and report dry-runs"
  .venv/bin/python -m discord_openclaw_bridge.traveler_source_discovery --dry-run
  .venv/bin/python -m discord_openclaw_bridge.post_traveler_collection_report --dry-run
  printf "[%s] traveler-collection-report dry-run complete\n" "$(date -Is)"
  exit 0
fi

DISCOVERY_EXIT=0
.venv/bin/python -m discord_openclaw_bridge.traveler_source_discovery || DISCOVERY_EXIT=$?
printf "[%s] traveler-source-discovery done (exit=%s)\n" "$(date -Is)" "${DISCOVERY_EXIT}"
if [ "${DISCOVERY_EXIT}" != "0" ] && [ "${ALLOW_STALE_TRAVELER_REPORT:-0}" != "1" ]; then
  printf "[%s] traveler-collection-report blocked because discovery failed; set ALLOW_STALE_TRAVELER_REPORT=1 to publish stale queue report\n" "$(date -Is)"
  exit "${DISCOVERY_EXIT}"
fi

CLI_EXIT=0
.venv/bin/python -m discord_openclaw_bridge.post_traveler_collection_report || CLI_EXIT=$?
printf "[%s] traveler-collection-report done (exit=%s discovery_exit=%s)\n" "$(date -Is)" "${CLI_EXIT}" "${DISCOVERY_EXIT}"
exit "${CLI_EXIT}"
