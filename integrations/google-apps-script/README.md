# Google Apps Script Gmail Newsletter Archive

This is the fallback production path when Google Cloud OAuth for `gmail.readonly`
is blocked by app verification, Workspace policy, or test-user restrictions.

## Install

1. Open <https://script.google.com/> and create a new Apps Script project.
2. Paste `newsletter_archive_to_discord.gs` into `Code.gs`.
3. Set Script Properties:

   - `DISCORD_WEBHOOK_URL`: Discord webhook URL for `아카이브룸/뉴스레타-수집` (**recommended; avoids Apps Script → Discord Bot API 40333**)
   - `SENDER_ALLOWLIST`: comma-separated senders/domains, for example
     `substack.com,openai.com,anthropic.com,deepmind.google,semianalysis.com`
   - Optional `DISCORD_CHANNEL_ID`: `1500839270921801879` bot-token fallback
   - Optional `DISCORD_BOT_TOKEN`: bot-token fallback; may fail from Apps Script with Discord/Cloudflare 40333
   - Optional `GMAIL_QUERY`: default `newer_than:7d`
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
