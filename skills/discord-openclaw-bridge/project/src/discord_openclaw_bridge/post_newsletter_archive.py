from __future__ import annotations

import asyncio
import os
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .post_card_news import (
    FORUM_CHANNEL_TYPES,
    GENERIC_TOPIC,
    TOPIC_PRIORITY,
    _clean,
    _clean_title,
    _latest_archive_path,
    _load_archive,
    _sanitize_public_url,
)
from .post_newsletter import (
    DISCORD_SUPPRESS_EMBEDS_FLAG,
    NewsletterPostConfigError,
    _delete_message_with_rate_limit,
    _load_dotenv,
    _post_message_with_rate_limit,
    _required_snowflake,
)

DEFAULT_NEWSLETTER_ARCHIVE_CHANNEL_ID = "1501073491921993758"
NEWSLETTER_ARCHIVE_TITLE = "집현전-Claw 뉴스레타 아카이브"
NEWSLETTER_ARCHIVE_THREAD_NAME_MARKERS = ("뉴스레타 아카이브",)
DISCORD_MESSAGE_LIMIT = 1900


def _topic_label(item: dict[str, Any]) -> str:
    return _clean(
        item.get("primary_topic_display")
        or item.get("topic")
        or item.get("primary_topic")
        or GENERIC_TOPIC,
        limit=70,
    )


def _description(item: dict[str, Any], *, limit: int = 180) -> str:
    raw_summary = item.get("summary_lines") or item.get("summaryLines") or []
    if isinstance(raw_summary, list):
        for line in raw_summary:
            text = _clean(line, limit=limit)
            if text:
                return text
    for key in ("public_excerpt", "article_description", "summary", "snippet", "description"):
        text = _clean(item.get(key), limit=limit)
        if text:
            return text
    return "공개 요약이 부족해 원문 제목과 링크를 후속 검토 대상으로 보존합니다."


def _title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "원문 링크", limit=120)


def _source_meta(item: dict[str, Any]) -> str:
    source = _clean(item.get("source_name") or item.get("sender") or "수집 경로 미상", limit=70)
    kind = _clean(item.get("kind") or "post", limit=30)
    received = _clean(item.get("received_at") or item.get("published_at") or "", limit=60)
    parts = [source, f"`{kind}`"]
    if received:
        parts.insert(1, received)
    return " · ".join(parts)


def _group_items_by_topic(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    grouped: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in items:
        url = _sanitize_public_url(_clean(item.get("url")))
        if not url:
            continue
        topic = _topic_label(item)
        grouped.setdefault(topic, []).append(item)
    return sorted(
        grouped.items(),
        key=lambda pair: (TOPIC_PRIORITY.get(pair[0], 999), -len(pair[1]), pair[0]),
    )


def _split_discord_messages(text: str, *, max_chars: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}".strip() if current else block.strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(block) <= max_chars:
            current = block.strip()
            continue
        start = 0
        while start < len(block):
            chunks.append(block[start : start + max_chars].rstrip())
            start += max_chars
        current = ""
    if current:
        chunks.append(current)
    return chunks


def render_newsletter_archive_messages(
    payload: dict[str, Any],
    *,
    max_items_per_topic: int = 12,
) -> list[str]:
    run_date = _clean(payload.get("date") or date.today().isoformat())
    items = [item for item in payload.get("items") or [] if isinstance(item, dict)]
    grouped = _group_items_by_topic(items)
    total_rendered = sum(min(len(topic_items), max_items_per_topic) for _topic, topic_items in grouped)
    total_links = sum(len(topic_items) for _topic, topic_items in grouped)
    lines: list[str] = [
        f"**{NEWSLETTER_ARCHIVE_TITLE} — {run_date}**",
        "",
        "> 토픽별로 수집된 공개 원본 링크와 간단 설명만 정리합니다.",
        "> 메일 본문, 토큰, 비밀값, 비공개 요약은 게시하지 않습니다.",
        "",
        f"- 토픽: {len(grouped)}개",
        f"- 공개 원본 링크: {total_links}개",
    ]
    if total_rendered != total_links:
        lines.append(f"- 표시: 토픽당 최대 {max_items_per_topic}개, 추가 링크는 raw archive에 보존")
    lines.append("")

    if not grouped:
        lines += [
            "### 수집 결과 없음",
            "- 공개 원본 링크가 없어 아카이브를 생성하지 않았습니다.",
        ]
        return _split_discord_messages("\n".join(lines))

    for topic, topic_items in grouped:
        lines += ["━━━━━━━━━━━━━━━━━━━━", f"### {topic} ({len(topic_items)}개)"]
        for item in topic_items[:max_items_per_topic]:
            title = _title(item)
            url = _sanitize_public_url(_clean(item.get("url")))
            description = _description(item)
            lines.append(f"- [{title}]({url}) — {description}")
            lines.append(f"  - 수집: {_source_meta(item)}")
        remaining = len(topic_items) - max_items_per_topic
        if remaining > 0:
            lines.append(f"- 추가 원본 링크 {remaining}개는 raw archive에 보존")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━",
        "운영 메모: 이 아카이브는 카드뉴스/블로그 해석본이 아니라 원본 링크 인덱스입니다.",
    ]
    return _split_discord_messages("\n".join(lines))


def _is_newsletter_archive_bot_message(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    author = message.get("author") if isinstance(message.get("author"), dict) else {}
    return bool(author.get("bot")) and NEWSLETTER_ARCHIVE_TITLE in content


async def _purge_previous_archive_messages(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
) -> int:
    response = await client.get(url, headers=headers, params={"limit": 100})
    response.raise_for_status()
    purged = 0
    for message in response.json():
        if _is_newsletter_archive_bot_message(message):
            await _delete_message_with_rate_limit(client, f"{url}/{message['id']}", headers=headers)
            purged += 1
    return purged


async def _purge_previous_archive_threads(
    client: httpx.AsyncClient,
    active_threads_url: str,
    *,
    headers: dict[str, str],
) -> int:
    response = await client.get(active_threads_url, headers=headers)
    response.raise_for_status()
    purged = 0
    for thread in response.json().get("threads") or []:
        name = str(thread.get("name") or "")
        if not any(marker in name for marker in NEWSLETTER_ARCHIVE_THREAD_NAME_MARKERS):
            continue
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            continue
        patch = await client.patch(
            f"https://discord.com/api/v10/channels/{thread_id}",
            headers=headers,
            json={"archived": True, "locked": False},
        )
        patch.raise_for_status()
        purged += 1
    return purged


async def _create_forum_archive_thread(
    client: httpx.AsyncClient,
    forum_url: str,
    *,
    headers: dict[str, str],
    name: str,
    content: str,
) -> str:
    response = await client.post(
        f"{forum_url}/threads",
        headers=headers,
        json={
            "name": _clean(name, limit=90),
            "auto_archive_duration": 1440,
            "message": {
                "content": content,
                "allowed_mentions": {"parse": []},
                "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
            },
        },
    )
    response.raise_for_status()
    thread_id = str(response.json().get("id") or "")
    if not thread_id:
        raise NewsletterPostConfigError("Discord forum thread creation returned no thread id")
    return thread_id


async def run() -> None:
    _load_dotenv(Path.cwd() / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise NewsletterPostConfigError("missing required env var: DISCORD_BOT_TOKEN")
    channel_raw = os.environ.get("DISCORD_NEWSLETTER_ARCHIVE_CHANNEL_ID", DEFAULT_NEWSLETTER_ARCHIVE_CHANNEL_ID).strip()
    os.environ["DISCORD_NEWSLETTER_ARCHIVE_CHANNEL_ID"] = channel_raw
    channel_id = _required_snowflake("DISCORD_NEWSLETTER_ARCHIVE_CHANNEL_ID")
    source = Path(os.environ.get("NEWSLETTER_ARCHIVE_SOURCE", str(_latest_archive_path()))).expanduser()
    max_items_per_topic = int(os.environ.get("NEWSLETTER_ARCHIVE_MAX_ITEMS_PER_TOPIC", "12"))
    purge_previous = os.environ.get("DISCORD_PURGE_PREVIOUS_NEWSLETTER_ARCHIVE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    payload = _load_archive(source)
    messages = render_newsletter_archive_messages(payload, max_items_per_topic=max_items_per_topic)
    headers = {"Authorization": f"Bot {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        channel_response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
        channel_response.raise_for_status()
        channel_data = channel_response.json()
        channel_type = int(channel_data.get("type", 0))
        guild_id = str(channel_data.get("guild_id") or "")
        purged = 0
        target_channel_id = channel_id
        thread_id = ""
        if channel_type in FORUM_CHANNEL_TYPES:
            if purge_previous and guild_id:
                purged = await _purge_previous_archive_threads(
                    client,
                    f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                    headers=headers,
                )
            header_chunks = _split_discord_messages(messages[0])
            thread_id = await _create_forum_archive_thread(
                client,
                f"https://discord.com/api/v10/channels/{channel_id}",
                headers=headers,
                name=f"{_clean(payload.get('date') or date.today().isoformat())} 뉴스레타 아카이브",
                content=header_chunks[0],
            )
            target_channel_id = int(thread_id)
            messages_to_post = [*header_chunks[1:], *messages[1:]]
        else:
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            if purge_previous:
                purged = await _purge_previous_archive_messages(client, url, headers=headers)
            messages_to_post = messages
        post_url = f"https://discord.com/api/v10/channels/{target_channel_id}/messages"
        for message in messages_to_post:
            await _post_message_with_rate_limit(
                client,
                post_url,
                headers=headers,
                content=message,
                suppress_embeds=True,
            )
    thread_note = f" thread={thread_id}" if thread_id else ""
    print(f"posted newsletter archive to channel={channel_id}{thread_note} source={source} messages={len(messages)} purged={purged}")


def main() -> None:
    try:
        asyncio.run(run())
    except (NewsletterPostConfigError, httpx.HTTPError, ValueError) as exc:
        print(f"newsletter archive post failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
