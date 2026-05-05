from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_openclaw_bridge.post_card_news import (  # noqa: E402
    CARD_NEWS_TITLE,
    DISCORD_SUPPRESS_EMBEDS_FLAG,
    _create_forum_card_news_thread,
    _is_card_news_bot_message,
    _purge_previous_card_news_messages,
    render_card_news_messages,
)


def test_card_news_renderer_creates_readable_cards_without_part_counters() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "title": "Digest subject",
                "article_title": "GraphRAG systems benchmark",
                "url": "https://example.com/graphrag",
                "primary_topic_display": "검색/RAG/지식그래프",
                "topic_confidence": 1.0,
                "topic_reasons": ["rag", "knowledge graph"],
                "summary_lines": [
                    "The article presents graph-grounded retrieval agents.",
                    "It compares indexing, query planning, and answer grounding.",
                    "It helps researchers track accuracy and latency trade-offs.",
                ],
            },
            {
                "title": "Agent memory report",
                "url": "https://example.com/agent-memory",
                "primary_topic_display": "LLM/에이전트",
                "topic_confidence": 0.7,
                "topic_reasons": ["agent", "llm"],
                "summary_lines": ["Core", "Tech", "Impact"],
            },
        ],
    }

    messages = render_card_news_messages(payload, max_cards=2)

    assert CARD_NEWS_TITLE in messages[0]
    assert "선별 카드: 2개" in messages[0]
    assert "블로그형 기술 브리핑" in messages[0]
    assert "**제목**" in messages[1]
    assert "GraphRAG systems benchmark" in messages[1]
    assert "**토픽과 근거 수준**" in messages[1]
    assert "**3줄 요약**" in messages[1]
    assert "1. The article presents graph-grounded retrieval agents." in messages[1]
    assert "**왜 지금인가**" in messages[1]
    assert "**핵심 주장**" in messages[1]
    assert "**근거**" in messages[1]
    assert "**산업/현장 해석**" in messages[1]
    assert "**다음 질문**" in messages[1]
    assert "**출처**" in messages[1]
    assert "<https://example.com/graphrag>" in messages[1]
    assert "**Card" not in "\n".join(messages)
    assert not any("(1/" in message or "(2/" in message for message in messages)


def test_card_news_renderer_deduplicates_titles_and_prioritizes_topic_spread() -> None:
    payload = {
        "items": [
            {"article_title": "Same", "url": "https://a", "primary_topic_display": "검색/RAG/지식그래프"},
            {"article_title": "Same", "url": "https://b", "primary_topic_display": "검색/RAG/지식그래프"},
            {"article_title": "Vision", "url": "https://c", "primary_topic_display": "멀티모달/비전"},
        ]
    }

    messages = render_card_news_messages(payload, max_cards=3)
    joined = "\n".join(messages)

    assert joined.count("**제목**") == 2
    assert "Same" in joined
    assert "Vision" in joined


def test_card_news_bot_message_matcher_targets_only_card_news_bot_messages() -> None:
    assert _is_card_news_bot_message({"content": f"**{CARD_NEWS_TITLE}**", "author": {"bot": True}})
    assert not _is_card_news_bot_message({"content": f"**{CARD_NEWS_TITLE}**", "author": {"bot": False}})
    assert not _is_card_news_bot_message({"content": "집현전-Claw 뉴스레터 수집 브리핑", "author": {"bot": True}})


def test_card_news_purge_deletes_only_prior_card_news_messages() -> None:
    import httpx

    deleted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {"id": "1", "content": f"**{CARD_NEWS_TITLE}**\nold", "author": {"bot": True}},
                    {"id": "2", "content": "집현전-Claw 뉴스레터 수집 브리핑", "author": {"bot": True}},
                    {"id": "3", "content": f"**{CARD_NEWS_TITLE}**", "author": {"bot": False}},
                ],
                request=request,
            )
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204, request=request)
        raise AssertionError(json.dumps({"method": request.method}))

    async def scenario() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _purge_previous_card_news_messages(
                client,
                "https://discord.com/api/v10/channels/1/messages",
                headers={"Authorization": "Bot test"},
            )

    purged = asyncio.run(scenario())

    assert purged == 1
    assert deleted == ["1"]


def test_forum_thread_creation_uses_thread_starter_with_suppressed_embeds() -> None:
    import httpx

    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v10/channels/1501073491921993758/threads"
        captured_payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(201, json={"id": "1502000000000000000"}, request=request)

    async def scenario() -> str:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _create_forum_card_news_thread(
                client,
                "https://discord.com/api/v10/channels/1501073491921993758",
                headers={"Authorization": "Bot test"},
                name="2026-05-05 기술 브리핑 카드뉴스",
                content=f"**{CARD_NEWS_TITLE}**\nheader",
            )

    thread_id = asyncio.run(scenario())

    assert thread_id == "1502000000000000000"
    assert captured_payloads == [
        {
            "name": "2026-05-05 기술 브리핑 카드뉴스",
            "auto_archive_duration": 1440,
            "message": {
                "content": f"**{CARD_NEWS_TITLE}**\nheader",
                "allowed_mentions": {"parse": []},
                "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
            },
        }
    ]
