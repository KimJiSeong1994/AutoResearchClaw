# Google Apps Script Gmail Newsletter Archive

This is the fallback production path when Google Cloud OAuth for `gmail.readonly`
is blocked by app verification, Workspace policy, or test-user restrictions.

## Install

1. Open <https://script.google.com/> and create a new Apps Script project.
2. Paste `newsletter_archive_to_discord.gs` into `Code.gs`.
3. Set Script Properties:

   - `DISCORD_CHANNEL_ID`: `1500839270921801879`
   - `DISCORD_BOT_TOKEN`: Discord bot token with Send Messages permission
   - `SENDER_ALLOWLIST`: comma-separated senders/domains, for example
     `substack.com,openai.com,anthropic.com,deepmind.google,semianalysis.com`
   - Optional `GMAIL_QUERY`: default `newer_than:7d`
   - Optional `MAX_THREADS`: default `50`

4. Run `runNewsletterArchive` once and approve Gmail/UrlFetch permissions.
5. Run `installDailyNewsletterTrigger` once to schedule a daily 08:15-ish run.

## Privacy boundary

The script posts only metadata and extracted source URLs. Full email bodies are
used in memory for URL extraction but are not posted to Discord.
