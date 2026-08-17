#!/usr/bin/env bash
# Stable EC2 cron entrypoint. Delegates to the committed Traveler runner.
set -euo pipefail

WORKSPACE="${HERMES_WORKSPACE:-${OPENCLAW_WORKSPACE:-$HOME/.hermes/workspace}}"
PAPERWIKI_INTEREST_ENV="${PAPERWIKI_INTEREST_ENV:-$WORKSPACE/runtime/paperwiki-interest.env}"
if [[ -f "$PAPERWIKI_INTEREST_ENV" ]]; then
  # Contains only non-secret, public interest-export controls and paths.
  # shellcheck disable=SC1090
  set -a
  source "$PAPERWIKI_INTEREST_ENV"
  set +a
fi
PAPERWIKI_SCOUT_TOPICS="${PAPERWIKI_SCOUT_TOPICS:-$WORKSPACE/state/traveler-scout-topics.paperwiki.json}"
if [[ -z "${JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH:-}" && -f "$PAPERWIKI_SCOUT_TOPICS" ]]; then
  export JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH="$PAPERWIKI_SCOUT_TOPICS"
fi
exec "$WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-traveler-collection-report.sh" "$@"
