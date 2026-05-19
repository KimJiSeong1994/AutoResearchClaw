from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

from .publication_trust_gate import PublicationTrustGateError, run_publication_trust_gate

DISCORD_SUPPRESS_EMBEDS_FLAG = 1 << 2
NEWSLETTER_TITLE = "집현전-Claw 뉴스레터 수집 브리핑"


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


async def _post_message_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    content: str,
    suppress_embeds: bool = True,
    max_retries: int = 4,
) -> None:
    payload: dict[str, object] = {"content": content, "allowed_mentions": {"parse": []}}
    if suppress_embeds:
        payload["flags"] = DISCORD_SUPPRESS_EMBEDS_FLAG
    for attempt in range(max_retries + 1):
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code != 429:
            response.raise_for_status()
            return
        retry_after = 1.0
        try:
            retry_after = float(response.json().get("retry_after") or retry_after)
        except Exception:
            header_value = response.headers.get("retry-after")
            if header_value:
                try:
                    retry_after = float(header_value)
                except ValueError:
                    retry_after = 1.0
        if attempt >= max_retries:
            response.raise_for_status()
        await asyncio.sleep(min(max(retry_after, 0.25), 10.0))


async def _delete_message_with_rate_limit(
    client: httpx.AsyncClient,
    message_url: str,
    *,
    headers: dict[str, str],
    max_retries: int = 4,
) -> None:
    for attempt in range(max_retries + 1):
        response = await client.delete(message_url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return
        retry_after = 1.0
        try:
            retry_after = float(response.json().get("retry_after") or retry_after)
        except Exception:
            header_value = response.headers.get("retry-after")
            if header_value:
                try:
                    retry_after = float(header_value)
                except ValueError:
                    retry_after = 1.0
        if attempt >= max_retries:
            response.raise_for_status()
        await asyncio.sleep(min(max(retry_after, 0.25), 10.0))


def _is_newsletter_bot_message(message: dict[str, object]) -> bool:
    content = str(message.get("content") or "")
    author = message.get("author")
    author_is_bot = isinstance(author, dict) and bool(author.get("bot"))
    return author_is_bot and NEWSLETTER_TITLE in content


async def _purge_previous_newsletter_messages(
    client: httpx.AsyncClient,
    messages_url: str,
    *,
    headers: dict[str, str],
    limit: int = 50,
) -> int:
    response = await client.get(f"{messages_url}?limit={limit}", headers=headers)
    response.raise_for_status()
    deleted = 0
    for message in response.json():
        if not isinstance(message, dict) or not _is_newsletter_bot_message(message):
            continue
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        await _delete_message_with_rate_limit(client, f"{messages_url}/{message_id}", headers=headers)
        deleted += 1
    return deleted


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
    suppress_embeds = os.environ.get("DISCORD_SUPPRESS_EMBEDS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    purge_previous = os.environ.get("DISCORD_PURGE_PREVIOUS_NEWSLETTER", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    body = _load_message(source, max_chars=max_chars * 20)
    trust_summary = run_publication_trust_gate(source, surface="newsletter-briefing")
    messages = _split_newsletter_messages(body, max_chars=max_chars)

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        purged = 0
        if purge_previous:
            purged = await _purge_previous_newsletter_messages(client, url, headers=headers)
        for message in messages:
            await _post_message_with_rate_limit(
                client,
                url,
                headers=headers,
                content=message,
                suppress_embeds=suppress_embeds,
            )
    suffix = f" purged={purged}" if purge_previous else ""
    print(
        f"posted newsletter briefing to channel={channel_id} source={source} messages={len(messages)}"
        f" trust_gate={trust_summary.get('decision')}{suffix}"
    )


def main() -> None:
    try:
        asyncio.run(run())
    except (NewsletterPostConfigError, PublicationTrustGateError, httpx.HTTPError) as exc:
        print(f"newsletter post failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
