#!/usr/bin/env bash
# Install / replace the Discord card-news cron on the EC2 OpenClaw host.
#
# This mirrors the AI newsletter archive publishing schedule. The cron itself
# starts at the same UTC minute as the newsletter archive job; the runner waits a
# short guard interval before posting so the freshly archived items.json is ready.
#
# Usage:
#   bash skills/discord-openclaw-bridge/install-card-news-cron.sh
#   CARD_NEWS_CRON_SCHEDULE="0 23 * * *" bash skills/discord-openclaw-bridge/install-card-news-cron.sh
#   CARD_NEWS_DELAY_SECONDS=0 bash skills/discord-openclaw-bridge/install-card-news-cron.sh
set -euo pipefail

KEY_FILE="${KEY_FILE:-/Users/jiseong/git/PaperReviewAgent/jiseong.pem}"
REMOTE_HOST="${REMOTE_HOST:-ubuntu@52.79.96.56}"
REMOTE_WORKSPACE="${REMOTE_WORKSPACE:-~/.openclaw/workspace}"

# Same schedule as the current AI newsletter archive publisher:
# 23:00 UTC = 08:00 Asia/Seoul (KST) next day.
CARD_NEWS_CRON_SCHEDULE="${CARD_NEWS_CRON_SCHEDULE:-0 23 * * *}"
CARD_NEWS_DELAY_SECONDS="${CARD_NEWS_DELAY_SECONDS:-900}"
CARD_NEWS_MAX_CARDS="${CARD_NEWS_MAX_CARDS:-8}"
CARD_NEWS_HERO_IMAGE_PATH="${CARD_NEWS_HERO_IMAGE_PATH:-assets/cardnews-hero-2026-05-05-ai.png}"

ssh -i "$KEY_FILE" "$REMOTE_HOST" \
  "REMOTE_WORKSPACE='$REMOTE_WORKSPACE' \
   CARD_NEWS_CRON_SCHEDULE='$CARD_NEWS_CRON_SCHEDULE' \
   CARD_NEWS_DELAY_SECONDS='$CARD_NEWS_DELAY_SECONDS' \
   CARD_NEWS_MAX_CARDS='$CARD_NEWS_MAX_CARDS' \
   CARD_NEWS_HERO_IMAGE_PATH='$CARD_NEWS_HERO_IMAGE_PATH' \
   bash -s" <<'REMOTE'
set -euo pipefail
WORKSPACE="${REMOTE_WORKSPACE/#\~/$HOME}"
SCRIPT_DIR="$WORKSPACE/scripts"
RUNNER="$SCRIPT_DIR/card-news-discord.sh"
mkdir -p "$SCRIPT_DIR" "$WORKSPACE/logs"

cat > "$RUNNER" <<EOF_RUNNER
#!/usr/bin/env bash
set -euo pipefail
export PATH="\$HOME/.local/bin:\$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
export TZ=Asia/Seoul
WORKSPACE="$WORKSPACE"
PROJECT="\$WORKSPACE/skills/discord-openclaw-bridge/project"
LOG_DIR="\$WORKSPACE/logs"
mkdir -p "\$LOG_DIR"
exec >>"\$LOG_DIR/card-news-discord.log" 2>&1

printf "\\n[%s] card-news discord start\\n" "\$(date -Is)"
DELAY="\${CARD_NEWS_DELAY_SECONDS:-$CARD_NEWS_DELAY_SECONDS}"
if [ "\$DELAY" -gt 0 ]; then
  echo "waiting \$DELAY seconds so newsletter archive publish can finish"
  sleep "\$DELAY"
fi

cd "\$PROJECT"
export DISCORD_CARD_NEWS_MAX_CARDS="\${DISCORD_CARD_NEWS_MAX_CARDS:-$CARD_NEWS_MAX_CARDS}"
export DISCORD_CARD_NEWS_HERO_IMAGE_PATH="\${DISCORD_CARD_NEWS_HERO_IMAGE_PATH:-$CARD_NEWS_HERO_IMAGE_PATH}"

if [ ! -f "\$DISCORD_CARD_NEWS_HERO_IMAGE_PATH" ]; then
  echo "warning: hero image not found: \$DISCORD_CARD_NEWS_HERO_IMAGE_PATH; posting without attachment fallback"
fi

if [ "\${CARD_NEWS_DRY_RUN:-0}" = "1" ]; then
  echo "dry-run: would run .venv/bin/discord-openclaw-post-card-news"
  echo "dry-run: DISCORD_CARD_NEWS_MAX_CARDS=\$DISCORD_CARD_NEWS_MAX_CARDS"
  echo "dry-run: DISCORD_CARD_NEWS_HERO_IMAGE_PATH=\$DISCORD_CARD_NEWS_HERO_IMAGE_PATH"
  printf "[%s] card-news discord dry-run complete\\n" "\$(date -Is)"
  exit 0
fi

.venv/bin/discord-openclaw-post-card-news
printf "[%s] card-news discord done\\n" "\$(date -Is)"
EOF_RUNNER
chmod +x "$RUNNER"

TMP="$(mktemp)"
crontab -l 2>/dev/null | awk '
  /# BEGIN JIPHYEONJEON CARD NEWS/ {skip=1; next}
  /# END JIPHYEONJEON CARD NEWS/ {skip=0; next}
  !skip {print}
' | grep -v "card-news-discord.sh" > "$TMP" || true
cat >> "$TMP" <<EOF_CRON
# BEGIN JIPHYEONJEON CARD NEWS
# EC2 cron runs in UTC. 23:00 UTC = 08:00 Asia/Seoul (KST) next day.
$CARD_NEWS_CRON_SCHEDULE $RUNNER
# END JIPHYEONJEON CARD NEWS
EOF_CRON
crontab "$TMP"
rm -f "$TMP"

echo "installed card-news cron:"
crontab -l | grep -A3 -B1 "JIPHYEONJEON CARD NEWS"
REMOTE
