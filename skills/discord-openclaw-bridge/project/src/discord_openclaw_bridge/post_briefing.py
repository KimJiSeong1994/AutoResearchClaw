from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

from .briefing import render_briefing
from .config import ConfigError, load_config


async def run() -> None:
    config = load_config()
    briefing = render_briefing(config.briefing_source_path, max_chars=config.max_response_chars)
    url = f"https://discord.com/api/v10/channels/{config.allowed_channel_id}/messages"
    headers = {"Authorization": f"Bot {config.discord_bot_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, headers=headers, json={"content": briefing.body})
        response.raise_for_status()
    print(f"posted briefing to channel={config.allowed_channel_id} source={briefing.source_path}")


def main() -> None:
    try:
        asyncio.run(run())
    except (ConfigError, FileNotFoundError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
