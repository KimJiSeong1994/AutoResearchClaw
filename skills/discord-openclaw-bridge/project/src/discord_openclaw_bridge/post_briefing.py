from __future__ import annotations

import asyncio
from datetime import date
import sys

import httpx

from .briefing import render_briefing
from .config import ConfigError, load_config
from .post_newsletter import DISCORD_SUPPRESS_EMBEDS_FLAG

FORUM_CHANNEL_TYPES = {15, 16}


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
    headers = {"Authorization": f"Bot {config.discord_bot_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        channel_response = await client.get(
            f"https://discord.com/api/v10/channels/{config.allowed_channel_id}",
            headers=headers,
        )
        channel_response.raise_for_status()
        channel = channel_response.json()
        target_channel_id = config.allowed_channel_id
        thread_id = ""
        messages_to_post = messages
        if int(channel.get("type", 0)) in FORUM_CHANNEL_TYPES:
            thread_name = f"{date.today().isoformat()} 집현전 데일리 브리핑"
            initial_suffix = f"\n\n(1/{len(messages)})" if len(messages) > 1 else ""
            response = await client.post(
                f"https://discord.com/api/v10/channels/{config.allowed_channel_id}/threads",
                headers=headers,
                json={
                    "name": thread_name[:90],
                    "auto_archive_duration": 1440,
                    "message": {
                        "content": messages[0] + initial_suffix,
                        "allowed_mentions": {"parse": []},
                        "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
                    },
                },
            )
            response.raise_for_status()
            thread_id = str(response.json().get("id") or "")
            if not thread_id:
                raise ConfigError("Discord forum thread creation returned no thread id")
            target_channel_id = int(thread_id)
            messages_to_post = messages[1:]
        url = f"https://discord.com/api/v10/channels/{target_channel_id}/messages"
        total_messages = len(messages)
        start_index = total_messages - len(messages_to_post) + 1
        for idx, message in enumerate(messages_to_post, start=start_index):
            suffix = f"\n\n({idx}/{total_messages})" if total_messages > 1 else ""
            response = await client.post(url, headers=headers, json={"content": message + suffix})
            response.raise_for_status()
    thread_note = f" thread={thread_id}" if thread_id else ""
    print(
        f"posted briefing to channel={config.allowed_channel_id}{thread_note} "
        f"source={briefing.source_path} messages={len(messages)}"
    )


def main() -> None:
    try:
        asyncio.run(run())
    except (ConfigError, FileNotFoundError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
