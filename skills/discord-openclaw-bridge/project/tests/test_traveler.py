from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from discord_openclaw_bridge.miner import DiscordLinkMetadata
from discord_openclaw_bridge.traveler import (
    TravelerResearchRequest,
    TravelerSourceInput,
    default_research_queue_path,
    default_source_queue_path,
    record_research_request,
    record_source_candidate,
)
from discord_openclaw_bridge.traveler_evidence import default_evidence_path
from discord_openclaw_bridge.traveler_outcomes import default_ledger_path
from discord_openclaw_bridge.traveler_scout import default_scout_queue_path, default_scout_status_path
from discord_openclaw_bridge.traveler_source_discovery import default_discovery_status_path


def test_traveler_default_paths_use_hermes_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_WORKSPACE", raising=False)
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    for key in (
        "JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH",
        "JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH",
        "JIPHYEONJEON_TRAVELER_EVIDENCE_PATH",
        "JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH",
        "JIPHYEONJEON_TRAVELER_SCOUT_STATUS_PATH",
        "JIPHYEONJEON_TRAVELER_OUTCOME_LEDGER_PATH",
        "JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)

    assert default_source_queue_path() == Path.home() / ".hermes" / "workspace" / "review" / "jiphyeonjeon-traveler" / "source-candidates.jsonl"
    assert default_research_queue_path() == Path.home() / ".hermes" / "workspace" / "review" / "jiphyeonjeon-traveler" / "research-requests.jsonl"
    assert default_evidence_path() == Path.home() / ".hermes" / "workspace" / "review" / "jiphyeonjeon-traveler" / "evidence.jsonl"
    assert default_scout_queue_path() == Path.home() / ".hermes" / "workspace" / "review" / "jiphyeonjeon-traveler" / "scout-candidates.jsonl"
    assert default_scout_status_path() == Path.home() / ".hermes" / "workspace" / "state" / "traveler-scout-last-status.json"
    assert default_ledger_path() == Path.home() / ".hermes" / "workspace" / "state" / "traveler-outcome-ledger.jsonl"
    assert default_discovery_status_path() == Path.home() / ".hermes" / "workspace" / "state" / "traveler-source-discovery-last-status.json"


def test_openclaw_workspace_env_remains_rollback_compatibility(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rollback_workspace = tmp_path / "openclaw-rollback"
    monkeypatch.delenv("HERMES_WORKSPACE", raising=False)
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(rollback_workspace))
    monkeypatch.delenv("JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH", raising=False)

    assert default_source_queue_path() == rollback_workspace / "review" / "jiphyeonjeon-traveler" / "source-candidates.jsonl"

    hermes_workspace = tmp_path / "hermes-primary"
    monkeypatch.setenv("HERMES_WORKSPACE", str(hermes_workspace))

    assert default_source_queue_path() == hermes_workspace / "review" / "jiphyeonjeon-traveler" / "source-candidates.jsonl"


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


def test_record_source_candidate_preserves_paper_page_source_type(tmp_path: Path) -> None:
    queue = tmp_path / "source-candidates.jsonl"

    record_source_candidate(
        TravelerSourceInput(url="https://aclanthology.org/2026.eacl-long.8/", title="T2-RAGBench", source_type="paper_page"),
        queue_path=queue,
        created_at=datetime(2026, 5, 15, tzinfo=UTC),
    )

    row = json.loads(queue.read_text(encoding="utf-8").strip())
    assert row["source_type"] == "paper_page"
    assert "paper_page" in row["tags"]


def test_record_source_candidate_rejects_private_urls(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="공개 http/https"):
        record_source_candidate(
            TravelerSourceInput(url="http://127.0.0.1/source"),
            queue_path=tmp_path / "source-candidates.jsonl",
        )


def test_record_source_candidate_allows_requeue_after_rejected_test(tmp_path: Path) -> None:
    queue = tmp_path / "source-candidates.jsonl"
    source = TravelerSourceInput(url="https://example.com/source", title="Example Source")
    first = record_source_candidate(source, queue_path=queue, created_at=datetime(2026, 5, 15, tzinfo=UTC))
    rows = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    rows[0]["status"] = "rejected_test"
    queue.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")

    second = record_source_candidate(source, queue_path=queue, created_at=datetime(2026, 5, 16, tzinfo=UTC))

    assert first.accepted
    assert second.accepted
    assert len(queue.read_text(encoding="utf-8").splitlines()) == 2
