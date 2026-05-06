from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from discord_openclaw_bridge.miner import (
    DiscordLinkMetadata,
    extract_urls,
    record_message_links,
    record_miner_link,
    render_ack,
    sanitize_url,
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_sanitize_url_strips_secret_and_tracking_query_params() -> None:
    url = sanitize_url(
        "<https://Example.COM/article?utm_source=news&token=SECRET&ok=1&midtoken=x#private-fragment>."
    )

    assert url == "https://example.com/article?ok=1"


def test_extract_urls_deduplicates_sanitized_links() -> None:
    urls = extract_urls(
        "검토 요청 https://example.com/a?utm_source=discord 과 <https://example.com/a>. "
        "그리고 https://example.com/b)."
    )

    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_record_miner_link_writes_intake_and_claw_review_queue(tmp_path: Path) -> None:
    intake_path = tmp_path / "intake" / "links.jsonl"
    review_path = tmp_path / "review" / "queue.jsonl"
    created_at = datetime(2026, 5, 5, 1, 2, 3, tzinfo=timezone.utc)

    result = record_miner_link(
        url="https://example.com/research?utm_campaign=x&ok=1",
        title="Useful Agent Report",
        note="뉴스레터 후보로 검토 요청",
        intake_path=intake_path,
        review_queue_path=review_path,
        discord=DiscordLinkMetadata(guild_id=1, channel_id=2, message_id=3, user_id=4),
        created_at=created_at,
    )

    assert result.accepted
    assert result.intake_id.startswith("miner_")

    intake_rows = _read_jsonl(intake_path)
    review_rows = _read_jsonl(review_path)
    assert intake_rows == review_rows
    row = intake_rows[0]
    assert row["agent"] == "jiphyeonjeon-miner"
    assert row["reviewer"] == "jiphyeonjeon-claw"
    assert row["status"] == "pending_claw_review"
    assert row["review"]["newsletter_reflection"] == "blocked_until_approved"
    assert row["url"] == "https://example.com/research?ok=1"
    assert row["source"] == "discord_miner"
    assert row["summary"] == "뉴스레터 후보로 검토 요청"
    assert row["discord"] == {"guild_id": 1, "channel_id": 2, "message_id": 3, "user_id": 4}


def test_record_miner_link_is_idempotent_by_sanitized_url(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    first = record_miner_link(
        url="https://example.com/rag-agent?utm_source=discord",
        intake_path=intake_path,
        review_queue_path=review_path,
    )
    second = record_miner_link(
        url="https://example.com/rag-agent",
        title="New title",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert first.accepted
    assert second.duplicate
    assert len(_read_jsonl(intake_path)) == 1
    assert len(_read_jsonl(review_path)) == 1


def test_record_message_links_renders_discord_ack(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    results = record_message_links(
        message_text=(
            "검토 부탁 https://example.com/rag-agent "
            "https://example.com/rag-agent?utm_source=x https://example.com/llm-benchmark"
        ),
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert [result.status for result in results] == ["accepted", "accepted"]
    assert "링크 2개" in render_ack(results)
    assert "집현전-클로 검토 큐" in render_ack(results)


def test_record_miner_link_rejects_non_academic_non_technical_links(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://www.linkedin.com/jobs/view/123",
        title="Job Alert",
        note="채용공고 추천",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.rejected
    assert not intake_path.exists()
    assert not review_path.exists()
    assert "수집 제외" in render_ack([result])


def test_record_miner_link_does_not_accept_allowlist_host_in_query(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://example.com/post?next=arxiv.org",
        title="Generic market note",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.rejected
    assert not intake_path.exists()


def test_record_miner_link_accepts_privacy_security_evaluation_reports(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://example.com/research/privacy-evaluation-benchmark",
        title="Privacy evaluation benchmark for LLM agents",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.accepted
    assert len(_read_jsonl(intake_path)) == 1
