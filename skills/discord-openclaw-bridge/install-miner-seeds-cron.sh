#!/usr/bin/env bash
# Install / replace the miner-seeds cron on the EC2 OpenClaw host.
#
# Runs at 21:00 UTC = 06:00 KST, just after the paper-recommender daily-research
# (20:00 UTC = 05:00 KST) finishes — avoids network contention.
#
# Usage:
#   bash skills/discord-openclaw-bridge/install-miner-seeds-cron.sh
#   MINER_SEEDS_CRON_SCHEDULE="0 21 * * *" bash skills/discord-openclaw-bridge/install-miner-seeds-cron.sh
#
# Requires bash 3.2+ (macOS default). The validation uses a portable case
# pattern instead of [[ =~ ]] regex so the script can run from a developer
# laptop and from CI without depending on homebrew bash.
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-~/.openclaw/workspace}"

# 21:00 UTC = 06:00 Asia/Seoul (KST)
MINER_SEEDS_CRON_SCHEDULE="${MINER_SEEDS_CRON_SCHEDULE:-0 21 * * *}"

# Defense-in-depth: validate user-provided values before they enter the SSH
# heredoc. The blocklist uses a case pattern (bash 3.2+ portable) and
# explicitly covers both the legacy single-quote-escape vectors AND the
# command-substitution / process-substitution / newline vectors that the
# 2nd-pass review caught (backtick, parens, newline).
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

# Cron schedule: 5 fields of [0-9*/,-]+ separated by whitespace.
case "$MINER_SEEDS_CRON_SCHEDULE" in
  '' )
    echo "ERROR: MINER_SEEDS_CRON_SCHEDULE is empty" >&2
    exit 2
    ;;
  *[\'\"\$\\\;\&\|\<\>\`\(\)]*)
    echo "ERROR: MINER_SEEDS_CRON_SCHEDULE contains unsafe characters" >&2
    exit 2
    ;;
esac
# Verify shape: 5 whitespace-separated fields.
read -r _f1 _f2 _f3 _f4 _f5 _rest <<< "$MINER_SEEDS_CRON_SCHEDULE"
if [ -z "${_f5:-}" ] || [ -n "${_rest:-}" ]; then
  echo "ERROR: MINER_SEEDS_CRON_SCHEDULE must be 5 cron fields" >&2
  exit 2
fi

# printf %q escapes the values so the inline shell expansion above produces a
# single-token assignment even when the value contains spaces or quotes.
_RW_QUOTED=$(printf '%q' "$REMOTE_WORKSPACE")
_SCHED_QUOTED=$(printf '%q' "$MINER_SEEDS_CRON_SCHEDULE")

# rsync the committed runner script so EC2 always uses the same logic as the
# repo (no inline heredoc copy to drift from project/scripts/run-miner-seeds.sh).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_RUNNER="$SCRIPT_DIR/project/scripts/run-miner-seeds.sh"
if [ ! -f "$LOCAL_RUNNER" ]; then
  echo "ERROR: cannot find committed runner at $LOCAL_RUNNER" >&2
  exit 2
fi
rsync -az -e "ssh -i $KEY_FILE" "$LOCAL_RUNNER" "$REMOTE_HOST:.openclaw/workspace/scripts/miner-seeds.sh"

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_WORKSPACE=$_RW_QUOTED MINER_SEEDS_CRON_SCHEDULE=$_SCHED_QUOTED bash -s" <<'REMOTE'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
SCRIPT_DIR="$WORKSPACE/scripts"
RUNNER="$SCRIPT_DIR/miner-seeds.sh"
mkdir -p "$SCRIPT_DIR" "$WORKSPACE/logs"
chmod +x "$RUNNER"

# Replace any existing JIPHYEONJEON MINER SEEDS block. The marker pair handles
# idempotency by itself.
TMP="$(mktemp)"
crontab -l 2>/dev/null | awk '
  /# BEGIN JIPHYEONJEON MINER SEEDS/ {skip=1; next}
  /# END JIPHYEONJEON MINER SEEDS/ {skip=0; next}
  !skip {print}
' > "$TMP" || true
cat >> "$TMP" <<EOF_CRON
# BEGIN JIPHYEONJEON MINER SEEDS
# EC2 cron runs in UTC. 21:00 UTC = 06:00 Asia/Seoul (KST).
$MINER_SEEDS_CRON_SCHEDULE $RUNNER
# END JIPHYEONJEON MINER SEEDS
EOF_CRON
crontab "$TMP"
rm -f "$TMP"

echo "installed miner-seeds cron:"
crontab -l | grep -A3 -B1 "JIPHYEONJEON MINER SEEDS"
REMOTE
