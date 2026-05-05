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

The script posts only metadata, compact cardnews copy, and extracted source URLs.
Full email bodies are used in memory for URL extraction and public article-page
summaries but are not posted to Discord. The renderer mirrors
`skills/paper-recommender/docs/newsletter-cardnews-template-20260505.md`: 훅 →
맥락 → 핵심 변화 → 왜 중요한가 → 근거/출처 → 시사점 → CTA/저장 포인트.

Treat `RELAY_READ_TOKEN` as a secret. The web app `doGet` endpoint returns the
latest stored briefing and Gmail query to callers that provide this token, so
rotate it if the deployment URL or token is exposed.

When `INCLUDE_ALL_URLS=true`, every non-blocked URL found in matching mail can
be summarized or linked. Keep `GMAIL_QUERY`, `SENDER_ALLOWLIST`, and
`COLLECT_ALL_MAIL` narrow when mail may contain private workspaces, documents,
or internal URLs.

## Validation checklist

This repository does not include a `.clasp.json` or `appsscript.json` for this
standalone fallback script, so local validation is limited to host-side static
checks and pure-helper smoke tests.

Before pasting or deploying updates:

1. Copy the script to a temporary `.js` file and run `node --check` on the copy
   to catch parser-visible JavaScript syntax errors. Node does not parse `.gs`
   paths directly.
2. Smoke-test pure rendering helpers with sample public article text and verify
   that each item renders the shared cardnews output contract:
   - `훅`
   - `맥락`
   - `핵심 변화`
   - `왜 중요한가`
   - `근거/출처`
   - `시사점`
   - `CTA/저장 포인트`
3. Smoke-test taxonomy helpers against the Python fixture intent before
   deployment. At minimum, verify:
   - `research paper` falls back to `논문/리서치` instead of matching `search`
   - `benchmark` does not match `market`
   - `RAG agent` tie-breaks toward `검색/RAG/지식그래프`
   - strong `LLM agent` signals beat a generic GitHub/source-kind signal
   - pricing/enterprise/partnership signals map to `산업/제품 동향`
4. Grep the Apps Script file and this README for concrete Discord webhook URLs,
   bot tokens, and relay tokens. Only Script Property names should appear in
   source control.
5. In Apps Script, run `runNewsletterArchive` once with `DELIVERY_MODE=relay_pull`
   and review `LATEST_BRIEFING` before enabling the daily trigger.

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
