#!/usr/bin/env bash
# Install / replace the daily 집현전-여행자 collection-gap report cron on EC2.
# Default schedule: 13:00 UTC = 22:00 Asia/Seoul (KST).
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-~/.openclaw/workspace}"
TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE="${TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE:-0 13 * * *}"

case "$REMOTE_WORKSPACE" in
  *[\'\"\$\\\;\&\|\<\>\`\(\)]*)
    echo "ERROR: REMOTE_WORKSPACE contains unsafe shell characters" >&2
    exit 2
    ;;
  *$'\n'*)
    echo "ERROR: REMOTE_WORKSPACE contains a newline" >&2
    exit 2
    ;;
esac
case "$TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE" in
  '' ) echo "ERROR: TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE is empty" >&2; exit 2 ;;
  *[\'\"\$\\\;\&\|\<\>\`\(\)]*)
    echo "ERROR: TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE contains unsafe characters" >&2
    exit 2
    ;;
esac
read -r _f1 _f2 _f3 _f4 _f5 _rest <<< "$TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE"
if [ -z "${_f5:-}" ] || [ -n "${_rest:-}" ]; then
  echo "ERROR: TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE must be 5 cron fields" >&2
  exit 2
fi

_RW_QUOTED=$(printf '%q' "$REMOTE_WORKSPACE")
_SCHED_QUOTED=$(printf '%q' "$TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE")
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_RUNNER="$SCRIPT_DIR/project/scripts/run-traveler-collection-report.sh"
if [ ! -f "$LOCAL_RUNNER" ]; then
  echo "ERROR: cannot find committed runner at $LOCAL_RUNNER" >&2
  exit 2
fi
ssh -i "$KEY_FILE" "$REMOTE_HOST" "REMOTE_WORKSPACE=$_RW_QUOTED bash -s" <<'REMOTE_PREP'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
mkdir -p "$WORKSPACE/scripts" "$WORKSPACE/logs"
REMOTE_PREP

rsync -az -e "ssh -i $KEY_FILE" "$LOCAL_RUNNER" "$REMOTE_HOST:$REMOTE_WORKSPACE/scripts/traveler-collection-report.sh"

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_WORKSPACE=$_RW_QUOTED TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE=$_SCHED_QUOTED bash -s" <<'REMOTE'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
RUNNER="$WORKSPACE/scripts/traveler-collection-report.sh"
mkdir -p "$WORKSPACE/scripts" "$WORKSPACE/logs"
chmod +x "$RUNNER"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
crontab -l 2>/dev/null | awk '
  /# BEGIN JIPHYEONJEON TRAVELER COLLECTION REPORT/ {skip=1; next}
  /# END JIPHYEONJEON TRAVELER COLLECTION REPORT/ {skip=0; next}
  !skip {print}
' > "$TMP" || true
cat >> "$TMP" <<EOF_CRON
# BEGIN JIPHYEONJEON TRAVELER COLLECTION REPORT
# EC2 cron runs in UTC. 13:00 UTC = 22:00 Asia/Seoul (KST).
$TRAVELER_COLLECTION_REPORT_CRON_SCHEDULE $RUNNER
# END JIPHYEONJEON TRAVELER COLLECTION REPORT
EOF_CRON
crontab "$TMP"
echo "installed traveler-collection-report cron:"
crontab -l | grep -A3 -B1 "JIPHYEONJEON TRAVELER COLLECTION REPORT"
REMOTE
