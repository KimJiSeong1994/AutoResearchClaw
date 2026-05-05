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


def _strip_emoji(value: str) -> str:
    return "".join(
        char
        for char in value
        if not (
            "\U0001F000" <= char <= "\U0001FAFF"
            or "\u2600" <= char <= "\u27BF"
        )
    ).strip()


def _clean_title(value: object, *, limit: int | None = None) -> str:
    return _clean(_strip_emoji(str(value or "")), limit=limit)


def _clean_multiline(value: object) -> str:
    lines = [_clean(line) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


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
                break
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


def _title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "Untitled", limit=90)


def _raw_title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "Untitled")


def _confidence_text(value: object) -> str:
    if isinstance(value, (int, float)):
        if value < 0.5:
            return f"{float(value):.2f} · 잠정 분류"
        return f"{float(value):.2f}"
    return "잠정 분류"


def _topic_lens(topic: str) -> str:
    mapping = {
        "검색/RAG/지식그래프": "검색 정확도보다 지식 구조, 색인 품질, 평가 체계를 먼저 정리해야 하는 문제",
        "LLM/에이전트": "모델 성능보다 기억 구조, 도구 사용, 운영 안정성이 결과를 좌우하는 문제",
        "멀티모달/비전": "입력 양식이 늘어날수록 데이터 품질, 평가 기준, 적용 환경이 함께 바뀌는 문제",
        "인프라/배포": "연구 성능을 실제 서비스 비용, 지연시간, 운영 안정성으로 번역하는 문제",
        "오픈소스/코드": "기술 확산 속도와 재현 가능성이 커지는 대신 유지보수 책임이 분산되는 문제",
        "AI 안전/평가": "성능 경쟁을 넘어 실패 양상, 검증 프로토콜, 책임 경계를 제도화하는 문제",
        "산업/제품 동향": "기술 선택이 제품 전략, 조직 인센티브, 시장 포지셔닝으로 이어지는 문제",
        "논문/리서치": "새 방법의 기여가 평가 설정과 재현 조건에 얼마나 의존하는지 따져야 하는 문제",
    }
    return mapping.get(topic, "기술 변화가 연구·제품·현장 적용 조건을 어떻게 바꾸는지 확인해야 하는 문제")


def _next_question(topic: str) -> str:
    mapping = {
        "검색/RAG/지식그래프": "이 접근이 실제 질의 분포, 최신성 요구, 그래프 유지 비용까지 견딜 수 있는가.",
        "LLM/에이전트": "기억·도구·계획 구조가 장기 실행에서 오류 누적과 비용 증가를 줄이는가.",
        "멀티모달/비전": "벤치마크 개선이 실제 센서·문서·사용자 입력의 노이즈에서도 유지되는가.",
        "인프라/배포": "성능 이득이 배포 비용, 장애 대응, 관측 가능성 비용을 상쇄하는가.",
        "오픈소스/코드": "재사용 속도만큼 보안, 라이선스, 장기 유지보수 책임도 명확한가.",
        "AI 안전/평가": "평가 기준이 실제 실패 비용과 책임 소재를 충분히 반영하는가.",
        "산업/제품 동향": "제품 발표가 실제 사용량, 매출, 조직 생산성 변화로 이어지는가.",
        "논문/리서치": "주장된 개선이 다른 데이터셋, 구현, 평가 조건에서도 재현되는가.",
    }
    return mapping.get(topic, "원문에서 방법, 평가 조건, 한계가 어떤 근거로 제시되는지 확인해야 합니다.")


def _dedup_push(sections: list[tuple[str, str]], seen: set[str], label: str, body: str) -> None:
    text = _clean_multiline(body)
    if not text or text in seen:
        return
    sections.append((label, text))
    seen.add(text)


def _richness(item: dict[str, Any], *, raw_title: str) -> str:
    if _summary_lines(item) or len(_clean(item.get("article_description"))) >= 120 or item.get("why_now") or item.get("evidence"):
        return "rich"
    excerpt = _clean(item.get("public_excerpt") or item.get("article_description"))
    if excerpt and _clean_title(excerpt).lower() != raw_title.lower():
        return "lean"
    return "skeletal"


def _render_sections(sections: list[tuple[str, str]]) -> list[str]:
    rendered: list[str] = []
    for label, body in sections:
        rendered.extend([f"**{label}**", body, ""])
    if rendered and rendered[-1] == "":
        rendered.pop()
    return rendered


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
                "연구자가 기술 변화의 사실, 해석, 현장 함의를 함께 읽을 수 있도록 카드별 근거 두께에 맞춰 재구성했습니다.",
                f"선별 카드: {len(cards)}개 / 수집 항목: {len(items)}개",
                "구성: 상세 근거가 있는 카드는 논증형으로, 제목 중심 카드는 읽기 후보와 확인 질문 중심으로 축약합니다.",
            ]
        )
    ]
    for item in cards:
        raw_title = _raw_title(item)
        title = _title(item)
        topic = _clean(item.get("primary_topic_display") or "기타 테크 리포트", limit=60)
        confidence_text = _confidence_text(item.get("topic_confidence"))
        reasons = item.get("topic_reasons") or []
        if isinstance(reasons, list):
            reason_text = _clean(", ".join(str(reason) for reason in reasons[:4]), limit=120)
        else:
            reason_text = ""
        summary = _summary_lines(item)
        url = _clean(item.get("url"))
        source = _source_name(item)
        richness = _richness(item, raw_title=raw_title)
        excerpt = _clean_title(item.get("public_excerpt") or item.get("article_description"), limit=190)
        if excerpt.lower() == raw_title.lower():
            excerpt = ""
        lens = _topic_lens(topic)
        next_question = _next_question(topic)
        explicit_why_now = _clean(item.get("why_now"), limit=180)
        claim = _clean(item.get("claim") or item.get("thesis"), limit=180)
        mechanism = _clean(item.get("mechanism") or item.get("claim_mechanism"), limit=180)
        evidence = _clean(
            item.get("evidence"),
            limit=160,
        )
        sections: list[tuple[str, str]] = []
        seen: set[str] = {raw_title} if raw_title != title else set()
        _dedup_push(sections, seen, "제목", title)
        _dedup_push(sections, seen, "토픽과 근거 수준", f"{topic} · {confidence_text}")

        if summary:
            summary_body = "\n".join(f"{idx}. {line}" for idx, line in enumerate(summary, start=1))
            _dedup_push(sections, seen, "3줄 요약", summary_body)
        elif excerpt:
            _dedup_push(sections, seen, "발췌", excerpt)
        else:
            _dedup_push(
                sections,
                seen,
                "읽는 법",
                f"현재 수집본에는 상세 본문 요약이 없어, 이 카드는 `{title}`를 {topic} 영역의 후속 읽기 후보로 표시합니다.",
            )

        if explicit_why_now:
            _dedup_push(sections, seen, "왜 지금인가", explicit_why_now)
        elif richness == "rich" and summary:
            _dedup_push(sections, seen, "왜 지금인가", f"{summary[0]} 이 변화는 {lens}와 연결됩니다.")

        claim_lines: list[str] = []
        if claim:
            claim_lines.append(f"- 주장: {claim}")
        if mechanism:
            claim_lines.append(f"- 메커니즘: {mechanism}")
        if claim_lines:
            _dedup_push(sections, seen, "핵심 주장", "\n".join(claim_lines))

        evidence_lines = [f"- 출처: {source}"]
        if reason_text:
            evidence_lines.append(f"- 분류 근거: {reason_text}")
        if evidence and evidence.lower() != title.lower() and evidence not in seen:
            evidence_lines.append(f"- 확인된 단서: {evidence}")
        else:
            evidence_lines.append("- 근거 한계: 현재 카드에는 원문 제목·링크·토픽 분류까지만 반영되어 세부 방법과 수치는 원문 확인이 필요합니다.")
        _dedup_push(sections, seen, "근거", "\n".join(evidence_lines))

        if topic != "기타 테크 리포트":
            if richness == "rich":
                interpretation = f"{topic} 관점에서 이 항목은 {lens}입니다. 따라서 원문을 읽을 때 성능 수치뿐 아니라 데이터 조건, 평가 방식, 운영 비용을 함께 확인해야 합니다."
            else:
                interpretation = f"{topic} 분류의 읽기 후보입니다. 아직 본문 근거가 얇으므로 {lens}인지 원문에서 먼저 검증해야 합니다."
            _dedup_push(sections, seen, "산업/현장 해석", interpretation)

        _dedup_push(sections, seen, "다음 질문", next_question)
        _dedup_push(sections, seen, "출처", f"<{url}>")

        messages.append(
            "\n".join(
                [
                    "━━━━━━━━━━━━━━━━━━━━",
                    *_render_sections(sections),
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
