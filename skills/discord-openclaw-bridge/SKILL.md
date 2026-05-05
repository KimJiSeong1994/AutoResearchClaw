---
name: discord_openclaw_bridge
description: Operate the Discord bot bridge that exposes OpenClaw in a single allowlisted Discord guild/channel while keeping the OpenClaw gateway loopback-only.
---

# Discord OpenClaw Bridge

Use this skill when the user asks to install, inspect, verify, invite, or operate OpenClaw from Discord.

## Runtime shape

- Project dir: `~/.openclaw/workspace/skills/discord-openclaw-bridge/project`
- Service: `discord-openclaw-bridge.service` under the `ubuntu` systemd user
- Discord access: bot token from project `.env`
- OpenClaw access: loopback `http://127.0.0.1:18789/v1` plus token file `~/.openclaw_gateway_token`
- Default allowlist: guild `1500743272551813142`, channel `1500743273361440823`
- Briefing source: `DISCORD_BRIEFING_SOURCE`, defaulting to `~/.openclaw/workspace/reports/daily-trends-latest.md`
- Card-news source: `DISCORD_CARD_NEWS_SOURCE`, defaulting to the latest `NEWSLETTER_WIKI_ROOT/raw/newsletters/*/items.json`

## Primary commands

Run from this skill directory unless stated otherwise:

- `bash project/scripts/install.sh` — create venv, install package, write user service
- `bash project/scripts/status.sh` — service/log/config-safe status
- `bash project/scripts/invite-url.sh CLIENT_ID` — print the minimal OAuth2 install URL
- `bash project/scripts/restart.sh` — controlled restart after config changes
- `bash project/scripts/post-briefing.sh` — post the configured Jiphyeonjeon-Claw briefing once
- `bash project/scripts/post-newsletter-briefing.sh` — post the configured newsletter Markdown briefing once
- `bash project/scripts/post-card-news.sh` — render and post the latest newsletter raw archive as compact card-news messages

## Safety rules

- Keep OpenClaw gateway loopback-only; do not expose `18789` publicly for Discord.
- Do not print Discord bot token or OpenClaw gateway token.
- Keep card-news content limited to sanitized archive fields; never add raw email bodies, OAuth tokens, Script Properties, webhook URLs, or relay tokens to Discord output.
- Keep allowed guild/channel set unless the human explicitly widens scope.
- Prefer slash commands and mentions; do not request `MESSAGE_CONTENT` unless needed for mention/free-text behavior.
- Log metadata and errors, not full user prompts or secret values.
