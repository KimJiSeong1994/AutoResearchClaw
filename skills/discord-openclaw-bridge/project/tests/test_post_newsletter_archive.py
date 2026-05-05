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
            "https://discord.com/api/v10/channels/1501211608104566854",
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
