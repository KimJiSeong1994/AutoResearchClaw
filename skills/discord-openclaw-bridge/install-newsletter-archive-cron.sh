#!/usr/bin/env bash
# Install / repair the daily newsletter archive + card-news cron on EC2.
# Default schedule: 23:00 UTC = 08:00 Asia/Seoul (KST).
set -euo pipefail

KEY_FILE="${KEY_FILE:?Set KEY_FILE to your SSH private key path}"
REMOTE_HOST="${REMOTE_HOST:?Set REMOTE_HOST, for example ubuntu@example.com}"
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-~/.openclaw/workspace}"
NEWSLETTER_ARCHIVE_CRON_SCHEDULE="${NEWSLETTER_ARCHIVE_CRON_SCHEDULE:-0 23 * * *}"
INSTALL_DAILY_BRIEFING_CRON="${INSTALL_DAILY_BRIEFING_CRON:-1}"

case "$REMOTE_WORKSPACE" in
  *[\'\"\$\\\;\&\|\<\>\`\(\)]*) echo "ERROR: REMOTE_WORKSPACE contains unsafe shell characters" >&2; exit 2 ;;
  *$'\n'*) echo "ERROR: REMOTE_WORKSPACE contains a newline" >&2; exit 2 ;;
esac
case "$NEWSLETTER_ARCHIVE_CRON_SCHEDULE" in
  '') echo "ERROR: NEWSLETTER_ARCHIVE_CRON_SCHEDULE is empty" >&2; exit 2 ;;
  *[\'\"\$\\\;\&\|\<\>\`\(\)]*) echo "ERROR: NEWSLETTER_ARCHIVE_CRON_SCHEDULE contains unsafe characters" >&2; exit 2 ;;
esac
read -r _f1 _f2 _f3 _f4 _f5 _rest <<< "$NEWSLETTER_ARCHIVE_CRON_SCHEDULE"
if [[ -z "${_f5:-}" || -n "${_rest:-}" ]]; then
  echo "ERROR: NEWSLETTER_ARCHIVE_CRON_SCHEDULE must be 5 cron fields" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOCAL_NEWSLETTER_RUNNER="$SCRIPT_DIR/project/scripts/run-newsletter-archive-and-cardnews.sh"
LOCAL_DAILY_RUNNER="$SCRIPT_DIR/project/scripts/run-daily-jiphyeonjeon-briefing.sh"
LOCAL_NEWSLETTER_ENTRYPOINT="$REPO_DIR/scripts/newsletter-archive-and-cardnews.sh"
LOCAL_DAILY_ENTRYPOINT="$REPO_DIR/scripts/daily-jiphyeonjeon-briefing.sh"
for path in "$LOCAL_NEWSLETTER_RUNNER" "$LOCAL_DAILY_RUNNER" "$LOCAL_NEWSLETTER_ENTRYPOINT" "$LOCAL_DAILY_ENTRYPOINT"; do
  if [[ ! -f "$path" ]]; then
    echo "ERROR: cannot find committed runner at $path" >&2
    exit 2
  fi
done

_RW_QUOTED="$(printf '%q' "$REMOTE_WORKSPACE")"
_SCHED_QUOTED="$(printf '%q' "$NEWSLETTER_ARCHIVE_CRON_SCHEDULE")"
_INSTALL_DAILY_QUOTED="$(printf '%q' "$INSTALL_DAILY_BRIEFING_CRON")"

ssh -i "$KEY_FILE" "$REMOTE_HOST" "REMOTE_WORKSPACE=$_RW_QUOTED bash -s" <<'REMOTE_PREP'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
mkdir -p "$WORKSPACE/scripts" "$WORKSPACE/logs" "$WORKSPACE/.locks" "$WORKSPACE/skills/discord-openclaw-bridge/project/scripts"
REMOTE_PREP

rsync -az -e "ssh -i $KEY_FILE" "$LOCAL_NEWSLETTER_ENTRYPOINT" "$REMOTE_HOST:$REMOTE_WORKSPACE/scripts/newsletter-archive-and-cardnews.sh"
rsync -az -e "ssh -i $KEY_FILE" "$LOCAL_DAILY_ENTRYPOINT" "$REMOTE_HOST:$REMOTE_WORKSPACE/scripts/daily-jiphyeonjeon-briefing.sh"
rsync -az -e "ssh -i $KEY_FILE" "$LOCAL_NEWSLETTER_RUNNER" "$REMOTE_HOST:$REMOTE_WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-newsletter-archive-and-cardnews.sh"
rsync -az -e "ssh -i $KEY_FILE" "$LOCAL_DAILY_RUNNER" "$REMOTE_HOST:$REMOTE_WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-daily-jiphyeonjeon-briefing.sh"

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_WORKSPACE=$_RW_QUOTED NEWSLETTER_ARCHIVE_CRON_SCHEDULE=$_SCHED_QUOTED INSTALL_DAILY_BRIEFING_CRON=$_INSTALL_DAILY_QUOTED bash -s" <<'REMOTE'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
NEWSLETTER_RUNNER="$WORKSPACE/scripts/newsletter-archive-and-cardnews.sh"
DAILY_RUNNER="$WORKSPACE/scripts/daily-jiphyeonjeon-briefing.sh"
NEWSLETTER_SKILL_RUNNER="$WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-newsletter-archive-and-cardnews.sh"
DAILY_SKILL_RUNNER="$WORKSPACE/skills/discord-openclaw-bridge/project/scripts/run-daily-jiphyeonjeon-briefing.sh"
chmod +x "$NEWSLETTER_RUNNER" "$DAILY_RUNNER" "$NEWSLETTER_SKILL_RUNNER" "$DAILY_SKILL_RUNNER"

for runner in "$NEWSLETTER_RUNNER" "$DAILY_RUNNER" "$NEWSLETTER_SKILL_RUNNER" "$DAILY_SKILL_RUNNER"; do
  if [ ! -s "$runner" ]; then
    echo "ERROR: installed runner is missing or empty: $runner" >&2
    exit 2
  fi
done
bash -n "$NEWSLETTER_RUNNER" "$DAILY_RUNNER" "$NEWSLETTER_SKILL_RUNNER" "$DAILY_SKILL_RUNNER"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
crontab -l 2>/dev/null | awk '
  /# BEGIN JIPHYEONJEON DAILY BRIEFING/ {skip=1; next}
  /# END JIPHYEONJEON DAILY BRIEFING/ {skip=0; next}
  /# BEGIN JIPHYEONJEON NEWSLETTER ARCHIVE AND CARDNEWS/ {skip=1; next}
  /# END JIPHYEONJEON NEWSLETTER ARCHIVE AND CARDNEWS/ {skip=0; next}
  /newsletter-archive-and-cardnews\.sh/ {next}
  /daily-jiphyeonjeon-briefing\.sh/ {next}
  !skip {print}
' > "$TMP" || true

if [ "$INSTALL_DAILY_BRIEFING_CRON" != "0" ]; then
  cat >> "$TMP" <<EOF_DAILY
# BEGIN JIPHYEONJEON DAILY BRIEFING
# EC2 cron runs in UTC. 23:00 UTC = 08:00 Asia/Seoul (KST) next day.
$NEWSLETTER_ARCHIVE_CRON_SCHEDULE $DAILY_RUNNER
# END JIPHYEONJEON DAILY BRIEFING
EOF_DAILY
fi
cat >> "$TMP" <<EOF_NEWSLETTER
# BEGIN JIPHYEONJEON NEWSLETTER ARCHIVE AND CARDNEWS
# EC2 cron runs in UTC. 23:00 UTC = 08:00 Asia/Seoul (KST) next day.
$NEWSLETTER_ARCHIVE_CRON_SCHEDULE $NEWSLETTER_RUNNER
# END JIPHYEONJEON NEWSLETTER ARCHIVE AND CARDNEWS
EOF_NEWSLETTER

crontab "$TMP"
echo "installed newsletter archive/card-news cron:"
crontab -l | grep -A8 -B1 -E "JIPHYEONJEON DAILY BRIEFING|JIPHYEONJEON NEWSLETTER ARCHIVE"
REMOTE
