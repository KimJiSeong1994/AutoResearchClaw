#!/usr/bin/env bash
# Run the daily 집현전-여행자 additional-collection report.
# Intended cron schedule: 13:00 UTC = 22:00 Asia/Seoul (KST).
set -euo pipefail

export PATH="${HOME}/.local/bin:${HOME}/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
export TZ="${TZ:-Asia/Seoul}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -d "$SCRIPT_DIR/../.venv" ]; then
  PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  DEFAULT_WORKSPACE="$(cd "${PROJECT_DIR}/../../.." && pwd)"
else
  DEFAULT_WORKSPACE="${HERMES_WORKSPACE:-${OPENCLAW_WORKSPACE:-${HOME}/.openclaw/workspace}}"
  PROJECT_DIR="${DEFAULT_WORKSPACE}/skills/discord-openclaw-bridge/project"
fi
WORKSPACE="${HERMES_WORKSPACE:-${OPENCLAW_WORKSPACE:-${DEFAULT_WORKSPACE}}}"
LOG_DIR="${WORKSPACE}/logs"
TRAVELER_REVIEW_DIR="${WORKSPACE}/review/jiphyeonjeon-traveler"
TRAVELER_STATE_DIR="${WORKSPACE}/state"
MINER_INTAKE_DIR="${WORKSPACE}/intake/jiphyeonjeon-miner"
MINER_REVIEW_DIR="${WORKSPACE}/review/jiphyeonjeon-claw"
MANUAL_LINKS_DIR="${WORKSPACE}/manual_links"
export JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH="${JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH:-${TRAVELER_REVIEW_DIR}/research-requests.jsonl}"
export JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH="${JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH:-${TRAVELER_REVIEW_DIR}/source-candidates.jsonl}"
export JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH="${JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH:-${JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH}}"
export JIPHYEONJEON_TRAVELER_EVIDENCE_PATH="${JIPHYEONJEON_TRAVELER_EVIDENCE_PATH:-${TRAVELER_REVIEW_DIR}/evidence.jsonl}"
export JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH="${JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH:-${TRAVELER_STATE_DIR}/traveler-source-discovery-last-status.json}"
export JIPHYEONJEON_TRAVELER_SCOUT_STATUS_PATH="${JIPHYEONJEON_TRAVELER_SCOUT_STATUS_PATH:-${TRAVELER_STATE_DIR}/traveler-scout-last-status.json}"
export JIPHYEONJEON_TRAVELER_REPORT_STATUS_PATH="${JIPHYEONJEON_TRAVELER_REPORT_STATUS_PATH:-${TRAVELER_STATE_DIR}/traveler-collection-report-last-status.json}"
export JIPHYEONJEON_MINER_INTAKE_PATH="${JIPHYEONJEON_MINER_INTAKE_PATH:-${MINER_INTAKE_DIR}/links.jsonl}"
export JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH="${JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH:-${MINER_REVIEW_DIR}/link-review-queue.jsonl}"
export JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH="${JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH:-${MANUAL_LINKS_DIR}/approved-manual-links.jsonl}"
mkdir -p "${TRAVELER_REVIEW_DIR}" "${TRAVELER_STATE_DIR}" "${MINER_INTAKE_DIR}" "${MINER_REVIEW_DIR}" "${MANUAL_LINKS_DIR}"
LOG_FILE="${LOG_DIR}/traveler-collection-report.log"
REPO_DIR="$(cd "${PROJECT_DIR}/../../.." && pwd)"
DEFAULT_SCOUT_TOPICS_PATH="${REPO_DIR}/runtime/traveler-scout-topics.json"
PAPERWIKI_KG_DB="${PAPERWIKI_KG_DB:-${REPO_DIR}/.omx/reports/paperwiki-kg/persistent/paperwiki_kg.sqlite}"
PAPERWIKI_SCOUT_TOPICS_PATH="${JIPHYEONJEON_TRAVELER_PAPERWIKI_SCOUT_TOPICS_PATH:-${TRAVELER_STATE_DIR}/traveler-scout-topics.paperwiki.json}"
TRAVELER_SCOUT_MAX_TOPICS="${JIPHYEONJEON_TRAVELER_SCOUT_MAX_TOPICS:-4}"
SCOUT_TOPICS_ARGS=()
if [ -n "${JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH:-}" ]; then
  SCOUT_TOPICS_ARGS=(--topics-path "${JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH}")
  export JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_MODE="override"
  export JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_PATH="${JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH}"
elif [ -f "${DEFAULT_SCOUT_TOPICS_PATH}" ]; then
  SCOUT_TOPICS_ARGS=(--topics-path "${DEFAULT_SCOUT_TOPICS_PATH}")
  export JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_MODE="baseline"
  export JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_PATH="${DEFAULT_SCOUT_TOPICS_PATH}"
fi

mkdir -p "${LOG_DIR}"
find "${LOG_DIR}" -name "traveler-collection-report*.log" -mtime +14 -delete 2>/dev/null || true

exec >>"${LOG_FILE}" 2>&1
timestamp() {
  date +"%Y-%m-%dT%H:%M:%S%z"
}

prepare_scout_topics() {
  if [ -n "${JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH:-}" ]; then
    echo "traveler scout topics override configured: ${JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH}"
    return 0
  fi
  if [ "${JIPHYEONJEON_TRAVELER_ENABLE_PAPERWIKI_KG:-0}" != "1" ]; then
    echo "traveler scout topics: optional PaperWiki KG merge disabled; using baseline topics"
    export JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON="paperwiki_kg_disabled"
    return 0
  fi
  local kg_script="${REPO_DIR}/scripts/paperwiki_kg.py"
  if [ ! -f "${kg_script}" ] || [ ! -f "${DEFAULT_SCOUT_TOPICS_PATH}" ]; then
    echo "traveler scout topics: PaperWiki KG helper or base topics missing; using baseline topics"
    export JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON="paperwiki_helper_or_base_missing"
    return 0
  fi
  local tmp
  tmp="$(mktemp "${TRAVELER_STATE_DIR}/traveler-scout-topics.paperwiki.XXXXXX.json")"
  if .venv/bin/python "${kg_script}" scout-topics --base "${DEFAULT_SCOUT_TOPICS_PATH}" --db "${PAPERWIKI_KG_DB}" >"${tmp}" 2>>"${LOG_FILE}"; then
    if .venv/bin/python - "${tmp}" <<'PY'
from pathlib import Path
import sys
from discord_openclaw_bridge.traveler_scout import load_scout_topics
path = Path(sys.argv[1])
try:
    topics = load_scout_topics(path)
except Exception:
    raise SystemExit(1)
if not topics:
    raise SystemExit(1)
PY
    then
      local paperwiki_status
      local paperwiki_interests_used
      paperwiki_status="$(
        .venv/bin/python - "${tmp}" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(str(payload.get("paperwiki_status", "")))
PY
      )"
      paperwiki_interests_used="$(
        .venv/bin/python - "${tmp}" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(int(payload.get("paperwiki_interests_used", 0) or 0))
PY
      )"
      if [ "${paperwiki_status}" = "healthy" ] && [ "${paperwiki_interests_used}" -gt 0 ]; then
        mv "${tmp}" "${PAPERWIKI_SCOUT_TOPICS_PATH}"
        SCOUT_TOPICS_ARGS=(--topics-path "${PAPERWIKI_SCOUT_TOPICS_PATH}")
        export JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_MODE="paperwiki_kg"
        export JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_PATH="${PAPERWIKI_SCOUT_TOPICS_PATH}"
        export JIPHYEONJEON_TRAVELER_TOPICS_GENERATED_FROM="$(
          .venv/bin/python - "${PAPERWIKI_SCOUT_TOPICS_PATH}" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(json.dumps(payload.get("generated_from", {}), ensure_ascii=False, sort_keys=True))
PY
        )"
        export JIPHYEONJEON_TRAVELER_TOPICS_TRUST_POLICY="$(
          .venv/bin/python - "${PAPERWIKI_SCOUT_TOPICS_PATH}" <<'PY'
import json, sys
from pathlib import Path
payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(str(payload.get("trust_policy", "")))
PY
        )"
        unset JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON
        echo "traveler scout topics: using optional PaperWiki KG merge at ${PAPERWIKI_SCOUT_TOPICS_PATH}"
      else
        rm -f "${tmp}"
        export JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON="paperwiki_no_exported_interests_or_unhealthy"
        echo "traveler scout topics: PaperWiki KG had no public exported interests or was unhealthy; using baseline topics"
      fi
    else
      rm -f "${tmp}"
      export JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON="paperwiki_output_invalid"
      echo "traveler scout topics: PaperWiki KG output invalid; using baseline topics"
    fi
  else
    rm -f "${tmp}"
    export JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON="paperwiki_merge_failed"
    echo "traveler scout topics: PaperWiki KG merge failed; using baseline topics"
  fi
}

printf "\n[%s] traveler-collection-report start\n" "$(timestamp)"
cd "${PROJECT_DIR}"
prepare_scout_topics

if [ "${TRAVELER_COLLECTION_REPORT_DRY_RUN:-0}" = "1" ]; then
  echo "dry-run: running traveler scout, source discovery, and report dry-runs"
  .venv/bin/python -m discord_openclaw_bridge.traveler_scout "${SCOUT_TOPICS_ARGS[@]}" --max-topics "${TRAVELER_SCOUT_MAX_TOPICS}" --dry-run
  .venv/bin/python -m discord_openclaw_bridge.traveler_source_discovery --dry-run
  .venv/bin/python -m discord_openclaw_bridge.post_traveler_collection_report --dry-run
  printf "[%s] traveler-collection-report dry-run complete\n" "$(timestamp)"
  exit 0
fi

SCOUT_EXIT=0
.venv/bin/python -m discord_openclaw_bridge.traveler_scout "${SCOUT_TOPICS_ARGS[@]}" --max-topics "${TRAVELER_SCOUT_MAX_TOPICS}" || SCOUT_EXIT=$?
printf "[%s] traveler-scout done (exit=%s)\n" "$(timestamp)" "${SCOUT_EXIT}"
if [ "${SCOUT_EXIT}" != "0" ] && [ "${ALLOW_STALE_TRAVELER_REPORT:-0}" != "1" ]; then
  printf "[%s] traveler-collection-report blocked because scout failed; set ALLOW_STALE_TRAVELER_REPORT=1 to continue\n" "$(timestamp)"
  exit "${SCOUT_EXIT}"
fi

DISCOVERY_EXIT=0
.venv/bin/python -m discord_openclaw_bridge.traveler_source_discovery || DISCOVERY_EXIT=$?
printf "[%s] traveler-source-discovery done (exit=%s)\n" "$(timestamp)" "${DISCOVERY_EXIT}"
if [ "${DISCOVERY_EXIT}" != "0" ] && [ "${ALLOW_STALE_TRAVELER_REPORT:-0}" != "1" ]; then
  printf "[%s] traveler-collection-report blocked because discovery failed; set ALLOW_STALE_TRAVELER_REPORT=1 to publish stale queue report\n" "$(timestamp)"
  exit "${DISCOVERY_EXIT}"
fi

CLI_EXIT=0
.venv/bin/python -m discord_openclaw_bridge.post_traveler_collection_report || CLI_EXIT=$?
printf "[%s] traveler-collection-report done (exit=%s scout_exit=%s discovery_exit=%s)\n" "$(timestamp)" "${CLI_EXIT}" "${SCOUT_EXIT}" "${DISCOVERY_EXIT}"

# Advisory steps. These record and analyse outcomes; neither posts anything nor
# changes config, so a failure here must never fail the daily report.
# --ledger is explicit: its default is ~/.openclaw/workspace regardless of which
# workspace is running, so without this the ledger lands beside a different
# deployment's state while the report lands here.
TRAVELER_LEDGER_PATH="${JIPHYEONJEON_TRAVELER_OUTCOME_LEDGER_PATH:-${TRAVELER_STATE_DIR}/traveler-outcome-ledger.jsonl}"
OUTCOMES_EXIT=0
.venv/bin/python -m discord_openclaw_bridge.traveler_outcomes \
  --ledger "${TRAVELER_LEDGER_PATH}" \
  --report "${TRAVELER_STATE_DIR}/traveler-calibration-latest.json" || OUTCOMES_EXIT=$?
printf "[%s] traveler-outcomes done (exit=%s)\n" "$(timestamp)" "${OUTCOMES_EXIT}"

# propose is read-only: it prints what a human could choose to apply. `apply`
# needs an interactive --confirm and is deliberately never invoked here.
TUNE_EXIT=0
# Trailing X's: BSD mktemp does not substitute a template with a suffix after them.
TUNE_TMP="$(mktemp "${TRAVELER_STATE_DIR}/traveler-tuning-proposals.XXXXXX")"
# --ledger precedes the subcommand: it is a top-level argument, so `propose --ledger` fails.
if .venv/bin/python -m discord_openclaw_bridge.traveler_tuning \
  --ledger "${TRAVELER_LEDGER_PATH}" propose >"${TUNE_TMP}" 2>>"${LOG_FILE}"; then
  mv "${TUNE_TMP}" "${TRAVELER_STATE_DIR}/traveler-tuning-proposals.json"
else
  TUNE_EXIT=$?
  rm -f "${TUNE_TMP}"
fi
printf "[%s] traveler-tune propose done (exit=%s)\n" "$(timestamp)" "${TUNE_EXIT}"

exit "${CLI_EXIT}"
