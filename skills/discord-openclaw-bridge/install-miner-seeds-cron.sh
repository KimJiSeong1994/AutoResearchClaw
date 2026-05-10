#!/usr/bin/env bash
# Install / replace the miner-seeds cron on the EC2 OpenClaw host.
#
# Runs at 21:00 UTC = 06:00 KST, just after the paper-recommender daily-research
# (20:00 UTC = 05:00 KST) finishes — avoids network contention.
#
# Usage:
#   bash skills/discord-openclaw-bridge/install-miner-seeds-cron.sh
#   MINER_SEEDS_CRON_SCHEDULE="0 21 * * *" bash skills/discord-openclaw-bridge/install-miner-seeds-cron.sh
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-~/.openclaw/workspace}"

# 21:00 UTC = 06:00 Asia/Seoul (KST)
MINER_SEEDS_CRON_SCHEDULE="${MINER_SEEDS_CRON_SCHEDULE:-0 21 * * *}"

# Defense-in-depth: validate user-provided values before they enter the SSH
# heredoc. Cron schedule must be standard 5-field syntax; workspace must be a
# tilde- or absolute-rooted POSIX path. Reject single quotes / shell metacharacters
# so a hostile env var cannot break out of the inline `...='$VAR'...` quoting.
if ! [[ "$MINER_SEEDS_CRON_SCHEDULE" =~ ^[0-9*/,-]+([[:space:]]+[0-9*/,-]+){4}$ ]]; then
  echo "ERROR: MINER_SEEDS_CRON_SCHEDULE must be a 5-field cron expression" >&2
  exit 2
fi
if [[ "$REMOTE_WORKSPACE" =~ [\'\"\$\\\;\&\|\<\>] ]]; then
  echo "ERROR: REMOTE_WORKSPACE contains unsafe characters" >&2
  exit 2
fi

# printf %q escapes the values so the inline shell expansion above produces a
# single-token assignment even when the value contains spaces or quotes.
_RW_QUOTED=$(printf '%q' "$REMOTE_WORKSPACE")
_SCHED_QUOTED=$(printf '%q' "$MINER_SEEDS_CRON_SCHEDULE")

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_WORKSPACE=$_RW_QUOTED MINER_SEEDS_CRON_SCHEDULE=$_SCHED_QUOTED bash -s" <<'REMOTE'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
SCRIPT_DIR="$WORKSPACE/scripts"
RUNNER="$SCRIPT_DIR/miner-seeds.sh"
mkdir -p "$SCRIPT_DIR" "$WORKSPACE/logs"

cat > "$RUNNER" <<EOF_RUNNER
#!/usr/bin/env bash
set -euo pipefail
export PATH="\$HOME/.local/bin:\$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
export TZ=Asia/Seoul
WORKSPACE="$WORKSPACE"
PROJECT="\$WORKSPACE/skills/discord-openclaw-bridge/project"
LOG_DIR="\$WORKSPACE/logs"
LOG_FILE="\$LOG_DIR/miner-seeds.log"
mkdir -p "\$LOG_DIR"

# Rotate logs older than 14 days
find "\$LOG_DIR" -name "miner-seeds*.log" -mtime +14 -delete 2>/dev/null || true

exec >>"\$LOG_FILE" 2>&1

printf "\\n[%s] miner-seeds start\\n" "\$(date -Is)"

cd "\$PROJECT"

if [ "\${MINER_SEEDS_DRY_RUN:-0}" = "1" ]; then
  echo "dry-run: would run .venv/bin/discord-openclaw-miner-seeds"
  .venv/bin/discord-openclaw-miner-seeds --dry-run
  printf "[%s] miner-seeds dry-run complete\\n" "\$(date -Is)"
  exit 0
fi

CLI_EXIT=0
.venv/bin/discord-openclaw-miner-seeds || CLI_EXIT=\$?
printf "[%s] miner-seeds done (exit=%s)\\n" "\$(date -Is)" "\${CLI_EXIT}"

# Post the run summary to the 운영리포팅 forum. Discord posting failures must
# never fail the cron — collection is the source of truth, the report is for
# observability only.
if [ "\${MINER_SEEDS_SKIP_DISCORD_REPORT:-0}" != "1" ]; then
  .venv/bin/discord-openclaw-post-miner-seeds-report || \
    printf "[%s] WARN miner-seeds discord report failed (continuing)\\n" "\$(date -Is)"
fi

exit "\${CLI_EXIT}"
EOF_RUNNER
chmod +x "$RUNNER"

# Replace any existing JIPHYEONJEON MINER SEEDS block. The marker pair handles
# idempotency by itself; we no longer grep for "miner-seeds.sh" because that
# substring may legitimately appear in unrelated cron entries.
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
