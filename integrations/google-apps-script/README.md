# Google Apps Script Gmail Newsletter Archive

This is the fallback production path when Google Cloud OAuth for `gmail.readonly`
is blocked by app verification, Workspace policy, or test-user restrictions.

## Install

1. Open <https://script.google.com/> and create a new Apps Script project.
2. Paste `newsletter_archive_to_discord.gs` into `Code.gs`.
3. Set Script Properties:

   - `COLLECT_ALL_MAIL`: `true` to process every message matching `GMAIL_QUERY`
   - `SENDER_ALLOWLIST`: optional when `COLLECT_ALL_MAIL=true`; otherwise comma-separated senders/domains
   - `DELIVERY_MODE`: `relay_pull` (**recommended; avoids Apps Script → Discord 40333**)
   - `RELAY_READ_TOKEN`: shared token for the EC2 puller
   - Optional `DISCORD_WEBHOOK_URL`: direct Discord fallback; may fail with 40333
   - Optional `DISCORD_CHANNEL_ID`: `1500839270921801879` bot-token fallback
   - Optional `DISCORD_BOT_TOKEN`: bot-token fallback; may fail from Apps Script with Discord/Cloudflare 40333
   - Optional `GMAIL_QUERY`: default `newer_than:7d`
   - Optional `INCLUDE_ALL_URLS`: default `true`
   - Optional `FETCH_ARTICLE_DETAILS`: default `true`; fetches public article pages for richer summaries
   - Optional `MAX_THREADS`: default `50`

4. Run `runNewsletterArchive` once and approve Gmail/UrlFetch permissions.
5. Run `installDailyNewsletterTrigger` once to schedule a daily 08:15-ish run.

## Privacy boundary

The script posts only metadata and extracted source URLs. Full email bodies are
used in memory for URL extraction but are not posted to Discord.

## Discord 40333 note

If Apps Script raises `HTTP 403 {"message":"internal network error","code":40333}`,
use `DISCORD_WEBHOOK_URL` instead of `DISCORD_BOT_TOKEN`. This error is a
Discord/Cloudflare block against the direct Bot API request path from Apps
Script, not a normal Discord channel permission error.

## 40333-safe relay-pull mode

Set `DELIVERY_MODE=relay_pull`. In this mode Apps Script only reads Gmail and
stores the latest briefing in Script Properties. Deploy the script as a Web App
with access limited to anyone with the URL, then configure EC2 with the Web App
URL plus `RELAY_READ_TOKEN`. EC2 pulls the briefing and posts it to Discord, so
Google Apps Script never calls Discord directly.
