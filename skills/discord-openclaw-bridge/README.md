# Discord OpenClaw Bridge

Minimal Discord bot bridge for operating OpenClaw from one Discord channel.

Default target:

- Guild/server: `1500743272551813142`
- Channel: `1500743273361440823`
- OpenClaw gateway: `http://127.0.0.1:18789/v1`

The bridge runs on the EC2 OpenClaw host and calls the OpenClaw gateway over loopback only. It exposes:

- `/openclaw prompt:<text>` — ask OpenClaw from the allowlisted channel
- `/openclaw_status` — lightweight OpenClaw health check
- `/jiphyeonjeon_briefing` — post the latest Jiphyeonjeon-Claw AI briefing from `DISCORD_BRIEFING_SOURCE`
- mention replies, only if `DISCORD_ENABLE_MENTION_RESPONSES=1`

## Setup on EC2

```bash
cd ~/.openclaw/workspace/skills/discord-openclaw-bridge
cp project/.env.example project/.env
$EDITOR project/.env  # set DISCORD_BOT_TOKEN and optional DISCORD_CLIENT_ID
bash project/scripts/install.sh
systemctl --user start discord-openclaw-bridge.service
bash project/scripts/status.sh
```

To publish a briefing immediately after the token is configured:

```bash
bash project/scripts/post-briefing.sh
```

## Invite URL

```bash
bash project/scripts/invite-url.sh YOUR_DISCORD_CLIENT_ID
```

Install the app with scopes `bot applications.commands`, then restrict the app/bot to the target channel in Discord channel permissions or Server Settings → Integrations.

## Security defaults

- No `Administrator` permission requested by the invite helper.
- OpenClaw gateway remains loopback-only.
- Discord and OpenClaw tokens are read from local secret files/env only.
- Full prompts are not logged by default.
