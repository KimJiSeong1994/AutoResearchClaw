from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import OrderedDict
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .post_newsletter import (
    DISCORD_SUPPRESS_EMBEDS_FLAG,
    NewsletterPostConfigError,
    _delete_message_with_rate_limit,
    _load_dotenv,
    _post_message_with_rate_limit,
    _required_snowflake,
)

DEFAULT_CARD_NEWS_CHANNEL_ID = "1501073491921993758"
CARD_NEWS_TITLE = "집현전-Claw 카드뉴스"
FORUM_CHANNEL_TYPES = {15}
CARD_NEWS_THREAD_NAME_MARKERS = (
    "기술 브리핑 카드뉴스",
    "블로그 포스팅 워크플로우 카드뉴스",
)


def _clean(value: object, *, limit: int | None = None) -> str:
    text = " ".join(str(value or "").split()).strip()
    if limit is not None and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _latest_archive_path() -> Path:
    root = Path(os.environ.get("NEWSLETTER_WIKI_ROOT", str(Path.home() / ".openclaw" / "workspace" / "wiki"))).expanduser()
    raw_root = root / "raw" / "newsletters"
    today_path = raw_root / date.today().isoformat() / "items.json"
    if today_path.exists():
        return today_path
    candidates = sorted(raw_root.glob("*/items.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    raise NewsletterPostConfigError(f"newsletter raw archive not found under {raw_root}")


def _load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise NewsletterPostConfigError(f"card news archive source not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise NewsletterPostConfigError(f"invalid newsletter archive payload: {path}")
    return payload


def _summary_lines(item: dict[str, Any]) -> list[str]:
    raw = item.get("summary_lines") or item.get("summaryLines") or []
    lines: list[str] = []
    if isinstance(raw, list):
        for line in raw:
            text = _clean(line, limit=160)
            if text and text not in lines:
                lines.append(text)
            if len(lines) == 3:
                return lines
    fallback = [
        _clean(item.get("public_excerpt") or item.get("article_description") or item.get("title"), limit=160),
        _clean(f"기술 분류: {item.get('primary_topic_display') or '기타 테크 리포트'}", limit=160),
        "공개 원문 기준으로 방법, 평가 지표, 적용 가능성을 후속 검토합니다.",
    ]
    for line in fallback:
        if line and line not in lines:
            lines.append(line)
    while len(lines) < 3:
        lines.append("공개 원문 근거를 추가 확인합니다.")
    return lines[:3]


def _source_name(item: dict[str, Any]) -> str:
    return _clean(
        item.get("source_name")
        or item.get("sender_name")
        or item.get("newsletter_name")
        or item.get("sender")
        or "원문",
        limit=80,
    )


def _topic_groups(items: list[dict[str, Any]]) -> OrderedDict[str, list[dict[str, Any]]]:
    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for item in items:
        topic = _clean(item.get("primary_topic_display") or "기타 테크 리포트")
        groups.setdefault(topic, []).append(item)
    priority = {
        "검색/RAG/지식그래프": 10,
        "LLM/에이전트": 20,
        "멀티모달/비전": 30,
        "인프라/배포": 40,
        "오픈소스/코드": 50,
        "AI 안전/평가": 60,
        "산업/제품 동향": 70,
        "논문/리서치": 80,
        "기타 테크 리포트": 900,
    }
    return OrderedDict(sorted(groups.items(), key=lambda pair: (priority.get(pair[0], 800), -len(pair[1]), pair[0])))


def _select_cards(items: list[dict[str, Any]], *, max_cards: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for _topic, topic_items in _topic_groups(items).items():
        for item in topic_items:
            title = _clean(item.get("article_title") or item.get("title"), limit=140).lower()
            url = _clean(item.get("url"))
            key = title or url
            if not url or not key or key in seen_titles:
                continue
            seen_titles.add(key)
            selected.append(item)
            break
        if len(selected) >= max_cards:
            return selected
    if len(selected) < max_cards:
        for item in items:
            title = _clean(item.get("article_title") or item.get("title"), limit=140).lower()
            url = _clean(item.get("url"))
            key = title or url
            if not url or not key or key in seen_titles:
                continue
            seen_titles.add(key)
            selected.append(item)
            if len(selected) >= max_cards:
                break
    return selected


def render_card_news_messages(payload: dict[str, Any], *, max_cards: int = 8) -> list[str]:
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    cards = _select_cards(items, max_cards=max_cards)
    run_date = _clean(payload.get("date") or date.today().isoformat())
    messages = [
        "\n".join(
            [
                f"**{CARD_NEWS_TITLE} — 블로그형 기술 브리핑**",
                f"발행일: `{run_date}`",
                "연구자가 기술 변화의 사실, 해석, 현장 함의를 함께 읽을 수 있도록 블로그형 카드로 재구성했습니다.",
                f"선별 카드: {len(cards)}개 / 수집 항목: {len(items)}개",
                "구성: 제목 → 3줄 요약 → 왜 지금인가 → 핵심 주장 → 근거 → 산업/현장 해석 → 다음 질문 → 출처",
            ]
        )
    ]
    for item in cards:
        title = _clean(item.get("article_title") or item.get("title") or "Untitled", limit=70)
        topic = _clean(item.get("primary_topic_display") or "기타 테크 리포트", limit=60)
        confidence = item.get("topic_confidence")
        confidence_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "n/a"
        reasons = item.get("topic_reasons") or []
        if isinstance(reasons, list):
            reason_text = _clean(", ".join(str(reason) for reason in reasons[:4]) or "fallback", limit=120)
        else:
            reason_text = "fallback"
        summary = _summary_lines(item)
        url = _clean(item.get("url"))
        source = _source_name(item)
        why_now = _clean(
            item.get("why_now")
            or item.get("article_description")
            or item.get("public_excerpt")
            or f"{topic} 영역에서 새로 포착된 변화가 연구·제품·현장 적용 판단에 영향을 줍니다.",
            limit=180,
        )
        evidence = _clean(
            item.get("evidence")
            or item.get("article_title")
            or item.get("title")
            or "원문 공개 내용",
            limit=160,
        )
        messages.append(
            "\n".join(
                [
                    "━━━━━━━━━━━━━━━━━━━━",
                    "**제목**",
                    title,
                    "",
                    "**토픽과 근거 수준**",
                    f"{topic} · confidence {confidence_text}",
                    "",
                    "**3줄 요약**",
                    f"1. {summary[0]}",
                    f"2. {summary[1]}",
                    f"3. {summary[2]}",
                    "",
                    "**왜 지금인가**",
                    why_now,
                    "",
                    "**핵심 주장**",
                    f"- 변화: {_clean(summary[0], limit=130)}",
                    f"- 메커니즘: {_clean(summary[1], limit=130)}",
                    "",
                    "**근거**",
                    f"- 출처: {source}",
                    f"- 분류 근거: {reason_text}",
                    f"- 확인된 단서: {evidence}",
                    "",
                    "**산업/현장 해석**",
                    _clean(
                        "기술 성능만이 아니라 조직 도입 비용, 평가 지표, 공급망/운영 조건, 연구자의 재현 가능성까지 함께 봐야 합니다.",
                        limit=190,
                    ),
                    "",
                    "**다음 질문**",
                    "이 변화가 실제 현장 성능, 비용 구조, 연구 재현성에서 같은 효과를 내는지 확인해야 합니다.",
                    "",
                    "**출처**",
                    f"<{url}>",
                ]
            )
        )
    return messages


def _is_card_news_bot_message(message: dict[str, object]) -> bool:
    content = str(message.get("content") or "")
    author = message.get("author")
    author_is_bot = isinstance(author, dict) and bool(author.get("bot"))
    return author_is_bot and CARD_NEWS_TITLE in content


async def _purge_previous_card_news_messages(
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
        if not isinstance(message, dict) or not _is_card_news_bot_message(message):
            continue
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        await _delete_message_with_rate_limit(client, f"{messages_url}/{message_id}", headers=headers)
        deleted += 1
    return deleted


async def _delete_channel_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
) -> None:
    while True:
        response = await client.delete(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return
        retry_after = float(response.json().get("retry_after", 1.0))
        await asyncio.sleep(retry_after)


async def _purge_previous_card_news_threads(
    client: httpx.AsyncClient,
    active_threads_url: str,
    *,
    headers: dict[str, str],
) -> int:
    response = await client.get(active_threads_url, headers=headers)
    response.raise_for_status()
    purged = 0
    threads = response.json().get("threads", [])
    if not isinstance(threads, list):
        return 0
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        name = str(thread.get("name") or "")
        thread_id = str(thread.get("id") or "")
        if not thread_id or not any(marker in name for marker in CARD_NEWS_THREAD_NAME_MARKERS):
            continue
        try:
            await _delete_channel_with_rate_limit(
                client,
                f"https://discord.com/api/v10/channels/{thread_id}",
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {403, 404}:
                raise
            continue
        else:
            purged += 1
    return purged


async def _create_forum_card_news_thread(
    client: httpx.AsyncClient,
    forum_url: str,
    *,
    headers: dict[str, str],
    name: str,
    content: str,
    hero_image_path: Path | None = None,
) -> str:
    payload = {
        "name": _clean(name, limit=90),
        "auto_archive_duration": 1440,
        "message": {
            "content": content,
            "allowed_mentions": {"parse": []},
            "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
        },
    }
    if hero_image_path and hero_image_path.exists():
        payload["message"]["attachments"] = [{"id": 0, "filename": hero_image_path.name}]
        response = await client.post(
            f"{forum_url}/threads",
            headers=headers,
            data={"payload_json": json.dumps(payload, ensure_ascii=False)},
            files={"files[0]": (hero_image_path.name, hero_image_path.read_bytes(), "image/png")},
        )
    else:
        response = await client.post(f"{forum_url}/threads", headers=headers, json=payload)
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
    channel_raw = os.environ.get("DISCORD_CARD_NEWS_CHANNEL_ID", DEFAULT_CARD_NEWS_CHANNEL_ID).strip()
    os.environ["DISCORD_CARD_NEWS_CHANNEL_ID"] = channel_raw
    channel_id = _required_snowflake("DISCORD_CARD_NEWS_CHANNEL_ID")
    source = Path(os.environ.get("DISCORD_CARD_NEWS_SOURCE", str(_latest_archive_path()))).expanduser()
    max_cards = int(os.environ.get("DISCORD_CARD_NEWS_MAX_CARDS", "8"))
    hero_image_raw = os.environ.get("DISCORD_CARD_NEWS_HERO_IMAGE_PATH", "").strip()
    hero_image_path = Path(hero_image_raw).expanduser() if hero_image_raw else None
    purge_previous = os.environ.get("DISCORD_PURGE_PREVIOUS_CARD_NEWS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    payload = _load_archive(source)
    messages = render_card_news_messages(payload, max_cards=max_cards)
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
            forum_url = f"https://discord.com/api/v10/channels/{channel_id}"
            if purge_previous and guild_id:
                purged = await _purge_previous_card_news_threads(
                    client,
                    f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                    headers=headers,
                )
            thread_id = await _create_forum_card_news_thread(
                client,
                forum_url,
                headers=headers,
                name=f"{_clean(payload.get('date') or date.today().isoformat())} 기술 브리핑 카드뉴스",
                content=messages[0],
                hero_image_path=hero_image_path,
            )
            target_channel_id = int(thread_id)
            messages_to_post = messages[1:]
        else:
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            if purge_previous:
                purged = await _purge_previous_card_news_messages(client, url, headers=headers)
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
    print(f"posted card news to channel={channel_id}{thread_note} source={source} messages={len(messages)} purged={purged}")


def main() -> None:
    try:
        asyncio.run(run())
    except (NewsletterPostConfigError, httpx.HTTPError) as exc:
        print(f"card news post failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
