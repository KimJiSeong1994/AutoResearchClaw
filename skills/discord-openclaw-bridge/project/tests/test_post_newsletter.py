from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_openclaw_bridge.post_newsletter import (  # noqa: E402
    NewsletterPostConfigError,
    _load_message,
    _post_message_with_rate_limit,
    _required_snowflake,
    _split_newsletter_messages,
)


def test_newsletter_message_loader_truncates() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "briefing.md"
        path.write_text("a" * 50, encoding="utf-8")

        body = _load_message(path, max_chars=20)

    assert body.endswith("…(briefing truncated)")


def test_newsletter_channel_requires_snowflake(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_NEWSLETTER_CHANNEL_ID", "not-a-number")

    import pytest

    with pytest.raises(NewsletterPostConfigError, match="snowflake"):
        _required_snowflake("DISCORD_NEWSLETTER_CHANNEL_ID")


def test_newsletter_splitter_preserves_topic_boundaries() -> None:
    text = "\n".join(
        [
            "**집현전-Claw 뉴스레터 수집 브리핑**",
            "## 토픽별 기술 리포트/뉴스레터 요약",
            "",
            "### 멀티모달/비전",
            "- 주요 아티클/논문: " + "a" * 80,
            "### LLM/에이전트",
            "- 주요 아티클/논문: " + "b" * 80,
            "### 검색/RAG/지식그래프",
            "- 주요 아티클/논문: " + "c" * 80,
        ]
    )

    chunks = _split_newsletter_messages(text, max_chars=180)

    assert len(chunks) >= 2
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert any("### LLM/에이전트" in chunk for chunk in chunks)
    assert any("### 검색/RAG/지식그래프" in chunk for chunk in chunks)


def test_newsletter_post_retries_discord_429(monkeypatch) -> None:
    import httpx

    calls: list[str] = []

    async def fake_sleep(_seconds: float) -> None:
        calls.append("sleep")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append("post")
        if calls.count("post") == 1:
            return httpx.Response(429, json={"retry_after": 0.01}, request=request)
        return httpx.Response(200, json={"id": "1"}, request=request)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await _post_message_with_rate_limit(
                client,
                "https://discord.com/api/v10/channels/1/messages",
                headers={"Authorization": "Bot test"},
                content="hello",
            )

    asyncio.run(scenario())

    assert calls == ["post", "sleep", "post"]
