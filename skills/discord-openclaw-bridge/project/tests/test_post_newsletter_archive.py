from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_openclaw_bridge.post_newsletter_archive import (  # noqa: E402
    NEWSLETTER_ARCHIVE_TITLE,
    _create_forum_archive_thread,
    _is_newsletter_archive_bot_message,
    _purge_previous_archive_threads,
    render_newsletter_archive_messages,
)


def test_newsletter_archive_groups_by_topic_with_original_links_and_descriptions() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "Ranking Engineer Agent",
                "url": "https://engineering.fb.com/rea?utm_source=newsletter&token=secret",
                "primary_topic_display": "LLM/에이전트",
                "summary_lines": ["Meta describes an autonomous agent for ads ranking ML operations."],
                "sender": "집현전-광부 승인 큐",
                "kind": "manual-link",
                "classification_text": "PRIVATE mailbox-only body token=abc123",
            },
            {
                "article_title": "GraphRAG Index",
                "url": "https://example.com/graphrag",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "Public article explains graph-grounded retrieval.",
                "sender": "AI Digest",
                "kind": "post",
            },
        ],
    }

    rendered = "\n".join(render_newsletter_archive_messages(payload, max_items_per_topic=12))

    assert NEWSLETTER_ARCHIVE_TITLE in rendered
    assert "### 검색/RAG/지식그래프 (1개)" in rendered
    assert "### LLM/에이전트 (1개)" in rendered
    assert "[Ranking Engineer Agent](https://engineering.fb.com/rea)" in rendered
    assert "autonomous agent for ads ranking" in rendered
    assert "[GraphRAG Index](https://example.com/graphrag)" in rendered
    assert "graph-grounded retrieval" in rendered
    assert "PRIVATE mailbox-only" not in rendered
    assert "token=secret" not in rendered
    assert "token=abc123" not in rendered


def test_newsletter_archive_respects_topic_item_limit() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": f"Agent item {idx}",
                "url": f"https://example.com/agent-{idx}",
                "primary_topic_display": "LLM/에이전트",
                "public_excerpt": "Short public description.",
            }
            for idx in range(3)
        ],
    }

    rendered = "\n".join(render_newsletter_archive_messages(payload, max_items_per_topic=2))

    assert "공개 원본 링크: 3개" in rendered
    assert "표시: 토픽당 최대 2개" in rendered
    assert "Agent item 0" in rendered
    assert "Agent item 1" in rendered
    assert "Agent item 2" not in rendered
    assert "추가 원본 링크 1개" in rendered


def test_newsletter_archive_deduplicates_sanitized_original_urls() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "GraphRAG canonical article",
                "url": "https://example.com/graphrag?utm_source=newsletter&token=secret#section",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "Canonical public description.",
            },
            {
                "article_title": "Duplicate GraphRAG title",
                "url": "https://EXAMPLE.com/graphrag/",
                "primary_topic_display": "LLM/에이전트",
                "public_excerpt": "Duplicate description should not render.",
            },
        ],
    }

    rendered = "\n".join(render_newsletter_archive_messages(payload, max_items_per_topic=12))

    assert "공개 원본 링크: 1개" in rendered
    assert "중복 제거: 1개" in rendered
    assert "GraphRAG canonical article" in rendered
    assert "Canonical public description." in rendered
    assert "Duplicate GraphRAG title" not in rendered
    assert "Duplicate description should not render." not in rendered
    assert "token=secret" not in rendered


def test_newsletter_archive_deduplicates_same_content_across_different_urls() -> None:
    payload = {
        "date": "2026-05-05",
        "items": [
            {
                "article_title": "LLMs, RAG, Agents, MCP",
                "url": "https://medium.com/@one/rag-agent-mcp",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "A visual explanation of RAG and agent orchestration.",
            },
            {
                "article_title": "LLMs, RAG, Agents, MCP",
                "url": "https://medium.com/@two/rag-agent-mcp-copy",
                "primary_topic_display": "LLM/에이전트",
                "public_excerpt": "A visual explanation of RAG and agent orchestration.",
            },
            {
                "article_title": "Different RAG benchmark",
                "url": "https://example.com/rag-benchmark",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "A distinct public benchmark summary.",
            },
        ],
    }

    rendered = "\n".join(render_newsletter_archive_messages(payload, max_items_per_topic=12))

    assert "공개 원본 링크: 2개" in rendered
    assert "중복 제거: 1개" in rendered
    assert rendered.count("LLMs, RAG, Agents, MCP") == 1
    assert "rag-agent-mcp-copy" not in rendered
    assert "Different RAG benchmark" in rendered



def test_newsletter_archive_collapses_repeated_graph_embedding_family() -> None:
    payload = {
        "date": "2026-05-23",
        "items": [
            {
                "article_title": "Dynamic graph embedding for anomaly detection",
                "url": "https://example.com/dynamic-graph",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "Dynamic graph embedding benchmark.",
            },
            {
                "article_title": "Heterogeneous graph embedding for recommendation",
                "url": "https://example.com/heterogeneous-graph",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "Heterogeneous graph embedding benchmark.",
            },
            {
                "article_title": "RAG evaluation benchmark",
                "url": "https://example.com/rag-eval",
                "primary_topic_display": "검색/RAG/지식그래프",
                "public_excerpt": "Distinct retrieval benchmark.",
            },
        ],
    }

    rendered = "\n".join(render_newsletter_archive_messages(payload, max_items_per_topic=12))

    assert "공개 원본 링크: 2개" in rendered
    assert "중복 제거: 1개" in rendered
    lower = rendered.lower()
    assert lower.count("dynamic graph embedding") + lower.count("heterogeneous graph embedding") <= 2
    assert "RAG evaluation benchmark" in rendered

def test_newsletter_archive_bot_message_matcher_targets_only_archive_bot_messages() -> None:
    assert _is_newsletter_archive_bot_message(
        {"content": f"**{NEWSLETTER_ARCHIVE_TITLE} — 2026-05-05**", "author": {"bot": True}}
    )
    assert not _is_newsletter_archive_bot_message(
        {"content": f"**{NEWSLETTER_ARCHIVE_TITLE} — 2026-05-05**", "author": {"bot": False}}
    )
    assert not _is_newsletter_archive_bot_message({"content": "다른 메시지", "author": {"bot": True}})


def test_forum_archive_thread_creation_suppresses_embeds_and_mentions() -> None:
    class FakeResponse:
        def __init__(self) -> None:
            self.payload: dict[str, object] | None = None

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "1501214384914169896"}

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def post(self, url: str, **kwargs: object) -> FakeResponse:
            self.calls.append({"url": url, **kwargs})
            return FakeResponse()

    client = FakeClient()

    thread_id = asyncio.run(
        _create_forum_archive_thread(
            client,  # type: ignore[arg-type]
            "https://discord.com/api/v10/channels/111111111111111111",
            headers={"Authorization": "Bot token"},
            name="2026-05-05 뉴스레타 아카이브",
            content="archive",
        )
    )

    assert thread_id == "1501214384914169896"
    payload = client.calls[0]["json"]
    assert isinstance(payload, dict)
    message = payload["message"]
    assert isinstance(message, dict)
    assert message["allowed_mentions"] == {"parse": []}
    assert message["flags"] == 4


def test_archive_thread_purge_targets_same_parent_and_same_date_name_only() -> None:
    class FakeResponse:
        def __init__(self, payload: dict[str, object] | None = None) -> None:
            self._payload = payload or {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    class FakeClient:
        def __init__(self) -> None:
            self.patched: list[tuple[str, dict[str, object]]] = []

        async def get(self, _url: str, **_kwargs: object) -> FakeResponse:
            return FakeResponse(
                {
                    "threads": [
                        {
                            "id": "same-parent-same-date",
                            "name": "2026-05-05 뉴스레타 아카이브",
                            "parent_id": "1501073491921993758",
                        },
                        {
                            "id": "wrong-parent-same-date",
                            "name": "2026-05-05 뉴스레타 아카이브",
                            "parent_id": "111111111111111111",
                        },
                        {
                            "id": "same-parent-other-date",
                            "name": "2026-05-04 뉴스레타 아카이브",
                            "parent_id": "1501073491921993758",
                        },
                    ]
                }
            )

        async def patch(self, url: str, **kwargs: object) -> FakeResponse:
            payload = kwargs["json"]
            assert isinstance(payload, dict)
            self.patched.append((url, payload))
            return FakeResponse()

    client = FakeClient()

    purged = asyncio.run(
        _purge_previous_archive_threads(
            client,  # type: ignore[arg-type]
            "https://discord.com/api/v10/guilds/222222222222222222/threads/active",
            headers={"Authorization": "Bot token"},
            parent_channel_id=1501073491921993758,
            thread_name="2026-05-05 뉴스레타 아카이브",
        )
    )

    assert purged == 1
    assert len(client.patched) == 1
    assert client.patched[0][0].endswith("/channels/same-parent-same-date")
    assert client.patched[0][1] == {"archived": True, "locked": False}
