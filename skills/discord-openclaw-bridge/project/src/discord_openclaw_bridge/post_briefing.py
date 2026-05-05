from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

from .briefing import render_briefing
from .config import ConfigError, load_config


def _split_briefing_messages(text: str, *, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines()
    chunks: list[str] = []
    current = ""
    for line in lines:
        candidate = (current + "\n" + line).strip() if current else line
        starts_section = line.startswith("### ") or line.startswith("## ")
        if len(candidate) > max_chars and current:
            chunks.append(current.strip())
            current = line
        elif starts_section and current and len(candidate) > int(max_chars * 0.72):
            chunks.append(current.strip())
            current = line
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())

    bounded: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            bounded.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            bounded.append(chunk[start : start + max_chars].rstrip())
            start += max_chars
    return bounded


async def run() -> None:
    config = load_config()
    briefing = render_briefing(config.briefing_source_path, max_chars=config.max_response_chars * 20)
    messages = _split_briefing_messages(briefing.body, max_chars=config.max_response_chars)
    url = f"https://discord.com/api/v10/channels/{config.allowed_channel_id}/messages"
    headers = {"Authorization": f"Bot {config.discord_bot_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        for idx, message in enumerate(messages, start=1):
            suffix = f"\n\n({idx}/{len(messages)})" if len(messages) > 1 else ""
            response = await client.post(url, headers=headers, json={"content": message + suffix})
            response.raise_for_status()
    print(f"posted briefing to channel={config.allowed_channel_id} source={briefing.source_path} messages={len(messages)}")


def main() -> None:
    try:
        asyncio.run(run())
    except (ConfigError, FileNotFoundError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
