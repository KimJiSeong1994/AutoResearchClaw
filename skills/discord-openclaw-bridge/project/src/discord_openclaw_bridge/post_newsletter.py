from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx


class NewsletterPostConfigError(RuntimeError):
    """Raised when newsletter posting is not safely configured."""


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _required_snowflake(name: str) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise NewsletterPostConfigError(f"missing required env var: {name}")
    try:
        return int(raw)
    except ValueError as exc:
        raise NewsletterPostConfigError(f"{name} must be a Discord snowflake integer") from exc


def _load_message(path: Path, *, max_chars: int) -> str:
    if not path.exists():
        raise NewsletterPostConfigError(f"newsletter briefing source not found: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise NewsletterPostConfigError(f"newsletter briefing source is empty: {path}")
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 24)].rstrip() + "\n…(briefing truncated)"
    return text


def _split_newsletter_messages(text: str, *, max_chars: int) -> list[str]:
    """Split a newsletter briefing into Discord-sized messages at topic boundaries."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    lines = text.splitlines()
    first_topic = next((idx for idx, line in enumerate(lines) if line.startswith("### ")), len(lines))
    header = "\n".join(lines[:first_topic]).strip()
    chunks: list[str] = []
    current = header

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for line in lines[first_topic:]:
        candidate = (current + "\n" + line).strip() if current else line
        if len(candidate) > max_chars and current:
            flush()
            if line.startswith("### ") and header:
                current = (header + "\n\n" + line).strip()
            else:
                current = line
        else:
            current = candidate
    flush()

    bounded: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            bounded.append(chunk)
            continue
        start = 0
        while start < len(chunk):
            part = chunk[start : start + max_chars]
            bounded.append(part.rstrip())
            start += max_chars
    return bounded


async def run() -> None:
    _load_dotenv(Path.cwd() / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise NewsletterPostConfigError("missing required env var: DISCORD_BOT_TOKEN")

    channel_id = _required_snowflake("DISCORD_NEWSLETTER_CHANNEL_ID")
    source = Path(
        os.environ.get(
            "DISCORD_NEWSLETTER_BRIEFING_SOURCE",
            str(Path.home() / ".openclaw" / "workspace" / "reports" / "newsletter-briefing-latest.md"),
        )
    ).expanduser()
    max_chars = int(os.environ.get("DISCORD_MAX_RESPONSE_CHARS", "1800"))
    body = _load_message(source, max_chars=max_chars * 20)
    messages = _split_newsletter_messages(body, max_chars=max_chars)

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        for idx, message in enumerate(messages, start=1):
            suffix = f"\n\n({idx}/{len(messages)})" if len(messages) > 1 else ""
            response = await client.post(url, headers=headers, json={"content": message + suffix})
            response.raise_for_status()
    print(f"posted newsletter briefing to channel={channel_id} source={source} messages={len(messages)}")


def main() -> None:
    try:
        asyncio.run(run())
    except (NewsletterPostConfigError, httpx.HTTPError) as exc:
        print(f"newsletter post failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
