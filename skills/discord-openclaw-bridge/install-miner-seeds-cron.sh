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

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_WORKSPACE='$REMOTE_WORKSPACE' \
   MINER_SEEDS_CRON_SCHEDULE='$MINER_SEEDS_CRON_SCHEDULE' \
   bash -s" <<'REMOTE'
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

.venv/bin/discord-openclaw-miner-seeds
printf "[%s] miner-seeds done\\n" "\$(date -Is)"
EOF_RUNNER
chmod +x "$RUNNER"

TMP="$(mktemp)"
crontab -l 2>/dev/null | awk '
  /# BEGIN JIPHYEONJEON MINER SEEDS/ {skip=1; next}
  /# END JIPHYEONJEON MINER SEEDS/ {skip=0; next}
  !skip {print}
' | grep -v "miner-seeds.sh" > "$TMP" || true
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
