from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discord_openclaw_bridge.miner import DiscordLinkMetadata
from discord_openclaw_bridge.traveler import (
    TravelerResearchRequest,
    TravelerSourceInput,
    record_research_request,
    record_source_candidate,
)


def test_record_research_request_requires_deep_many_source_review(tmp_path: Path) -> None:
    queue = tmp_path / "research-requests.jsonl"
    candidates = tmp_path / "source-candidates.jsonl"

    record = record_research_request(
        TravelerResearchRequest(topic="RAG and knowledge graph newsletters", min_sources_to_review=5),
        queue_path=queue,
        candidate_queue_path=candidates,
        discord=DiscordLinkMetadata(guild_id=1, channel_id=2, user_id=3),
        created_at=datetime(2026, 5, 15, tzinfo=UTC),
    )

    row = json.loads(queue.read_text(encoding="utf-8").strip())
    assert row == record
    assert row["status"] == "pending_deep_research"
    assert row["min_sources_to_review"] == 10
    assert row["acceptance_criteria"]["review_many_sources"] is True
    assert row["acceptance_criteria"]["no_single_url_fast_track"] is True
    assert row["candidate_queue_path"] == str(candidates)


def test_record_source_candidate_sanitizes_and_deduplicates_public_sources(tmp_path: Path) -> None:
    queue = tmp_path / "source-candidates.jsonl"
    source = TravelerSourceInput(
        url="https://example.com/archive?utm_source=x&token=secret&id=7",
        title="Example AI Research Archive",
        source_type="archive-page",
        reliability_note="Named editorial archive with stable public URLs.",
        cadence_note="Weekly archive page observed.",
        topic_fit="AI systems and RAG reports.",
    )

    first = record_source_candidate(source, queue_path=queue, created_at=datetime(2026, 5, 15, tzinfo=UTC))
    second = record_source_candidate(source, queue_path=queue, created_at=datetime(2026, 5, 15, tzinfo=UTC))

    assert first.accepted
    assert second.duplicate
    row = json.loads(queue.read_text(encoding="utf-8").strip())
    assert row["agent"] == "jiphyeonjeon-traveler"
    assert row["status"] == "pending_source_review"
    assert row["review"]["miner_seed_expansion"] == "blocked_until_reviewed"
    assert row["url"] == "https://example.com/archive?id=7"


def test_record_source_candidate_rejects_private_urls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="공개 http/https"):
        record_source_candidate(
            TravelerSourceInput(url="http://127.0.0.1/source"),
            queue_path=tmp_path / "source-candidates.jsonl",
        )
