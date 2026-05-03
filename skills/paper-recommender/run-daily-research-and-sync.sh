#!/usr/bin/env bash
set -euo pipefail

# Run the EC2 daily-research collection once, then publish/sync the resulting
# artifacts into the local LLM Wiki. Intended for local cron/launchd at 07:00 KST.
#
# If network/remote sync is unavailable, write a local fallback record under
# ~/Desktop/paper-wiki so the scheduled run leaves an inspectable daily trace
# instead of disappearing into cron logs.

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_PROJECT="${REMOTE_PROJECT:-~/.openclaw/workspace/projects/paper-recommender}"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LOG_DIR:-/Users/jiseong/git/AutoResearchClaw/.omx/logs/paper-recommender}"
PRIMARY_WIKI_ROOT="${WIKI_ROOT:-/Users/jiseong/Library/Mobile Documents/com~apple~CloudDocs/PaperWiki/PaperWiki}"
FALLBACK_WIKI_ROOT="${FALLBACK_WIKI_ROOT:-$HOME/Desktop/paper-wiki}"
RUN_DATE="${RUN_DATE:-$(date +%Y-%m-%d)}"
mkdir -p "$LOG_DIR"
TS="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/daily_research_and_wiki_${TS}.log"

write_fallback_note() {
  local reason="$1"
  local root="$FALLBACK_WIKI_ROOT"
  local raw_dir="$root/raw/autoresearch/$RUN_DATE"
  local pages_dir="$root/pages"
  local logs_dir="$root/logs"
  mkdir -p "$raw_dir" "$pages_dir" "$logs_dir"

  cp "$LOG_FILE" "$logs_dir/$(basename "$LOG_FILE")" 2>/dev/null || true

  cat > "$raw_dir/run-status.md" <<STATUS
---
date: "$RUN_DATE"
type: autoresearch-fallback-status
tags:
  - autoresearch
  - fallback
  - network-unavailable
---
# AutoResearch fallback status — $RUN_DATE

- Status: fallback recorded locally
- Reason: $reason
- Primary wiki root: $PRIMARY_WIKI_ROOT
- Fallback wiki root: $FALLBACK_WIKI_ROOT
- Log: logs/$(basename "$LOG_FILE")

No fresh EC2 result can be guaranteed when the network/remote step is unavailable.
Re-run this script after network recovery to collect and publish fresh artifacts.
STATUS

  cat > "$pages_dir/autoresearch-$RUN_DATE-fallback.md" <<PAGE
---
date: "$RUN_DATE"
type: autoresearch-fallback
tags:
  - autoresearch
  - fallback
  - network-unavailable
---
# Daily Research fallback — $RUN_DATE

> [!warning] Network/remote sync unavailable
> $reason

A local fallback record was saved because the scheduled collection could not fully publish to the primary LLM Wiki.

- Raw status: [[../raw/autoresearch/$RUN_DATE/run-status|run-status]]
- Log copy: logs/$(basename "$LOG_FILE")
PAGE

  echo "fallback saved to: $root"
}

{
  echo "== daily research + llm wiki sync $TS =="
  date
  echo "primary wiki root:  $PRIMARY_WIKI_ROOT"
  echo "fallback wiki root: $FALLBACK_WIKI_ROOT"
  echo

  echo "running remote daily-research on $REMOTE_HOST:$REMOTE_PROJECT"
  if ! ssh -i "$KEY_FILE" "$REMOTE_HOST" "bash $REMOTE_PROJECT/scripts/run_daily_research.sh"; then
    echo "remote daily-research failed or network unavailable"
    write_fallback_note "remote daily-research failed or network unavailable"
    exit 0
  fi

  echo
  echo "syncing/publishing to primary local LLM Wiki"
  if bash "$SKILL_DIR/sync-results.sh"; then
    echo "primary sync/publish succeeded"
  else
    echo "primary sync/publish failed; retrying with Desktop fallback wiki root"
    if WIKI_ROOT="$FALLBACK_WIKI_ROOT" bash "$SKILL_DIR/sync-results.sh"; then
      echo "fallback sync/publish succeeded"
    else
      echo "fallback sync/publish failed"
      write_fallback_note "primary and fallback sync/publish failed after remote collection"
      exit 0
    fi
  fi

  echo
  echo "done"
  date
} >>"$LOG_FILE" 2>&1

tail -n 40 "$LOG_FILE"
echo "log: $LOG_FILE"
