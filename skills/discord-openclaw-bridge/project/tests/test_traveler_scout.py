from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from discord_openclaw_bridge.traveler_scout import create_scout_requests, default_scout_queue_path, load_scout_topics


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_load_scout_topics_from_json_config(tmp_path: Path) -> None:
    config = tmp_path / "topics.json"
    config.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "id": "llm_agents",
                        "query": "LLM agents research engineering",
                        "scope": "public sources",
                        "min_sources_to_review": 8,
                        "max_candidates": 2,
                        "priority": "high",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    topics = load_scout_topics(config)

    assert len(topics) == 1
    assert topics[0].topic_id == "llm_agents"
    assert topics[0].min_sources_to_review == 10
    assert topics[0].max_candidates == 2
    assert topics[0].priority == "high"


def test_load_scout_topics_preserves_paperwiki_topic_metadata(tmp_path: Path) -> None:
    config = tmp_path / "topics.json"
    config.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "id": "llm_agents",
                        "query": "agent planning retrieval",
                        "scope": "public research sources for agent planning",
                        "priority": "high",
                        "source": "interest-note",
                        "paperwiki_interest_slug": "llm-agents",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    topics = load_scout_topics(config)

    assert topics[0].source == "interest-note"
    assert topics[0].paperwiki_interest_slug == "llm-agents"


def test_scout_dry_run_plans_without_queue_mutation(tmp_path: Path) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "scout-candidates.jsonl"

    result = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, dry_run=True)

    assert result["status"]["requests_planned"] == 1
    assert result["status"]["requests_created"] == 0
    assert result["requests"][0]["discovery_mode"] == "autonomous_scout"
    assert result["requests"][0]["candidate_queue_path"] == str(scout)
    assert not research.exists()
    assert not scout.exists()


def test_scout_run_appends_autonomous_research_request(tmp_path: Path) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "scout-candidates.jsonl"
    status = tmp_path / "status.json"

    result = create_scout_requests(
        topics=topics,
        research_queue_path=research,
        scout_queue_path=scout,
        status_path=status,
        dry_run=False,
    )

    rows = _jsonl(research)
    assert result["status"]["requests_created"] == 1
    assert rows[0]["status"] == "pending_deep_research"
    assert rows[0]["discovery_mode"] == "autonomous_scout"
    assert rows[0]["scout_topic_id"] == topics[0].topic_id
    assert rows[0]["max_candidates"] == topics[0].max_candidates
    assert rows[0]["candidate_queue_path"] == str(scout)
    assert json.loads(status.read_text(encoding="utf-8"))["scout_queue_path"] == str(scout)


def test_scout_records_paperwiki_topic_provenance(tmp_path: Path) -> None:
    config = tmp_path / "topics.json"
    config.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "id": "llm_agents",
                        "query": "agent planning retrieval",
                        "priority": "high",
                        "source": "interest-note",
                        "paperwiki_interest_slug": "llm-agents",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    topics = load_scout_topics(config)
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "source-candidates.jsonl"
    status = tmp_path / "status.json"

    result = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, status_path=status)

    rows = _jsonl(research)
    assert result["status"]["topic_sources"] == {"llm_agents": "interest-note"}
    assert result["status"]["paperwiki_interest_slugs"] == {"llm_agents": "llm-agents"}
    assert rows[0]["topic_source"] == "interest-note"
    assert rows[0]["paperwiki_interest_slug"] == "llm-agents"
    assert "source=interest-note" in rows[0]["requester_note"]
    assert json.loads(status.read_text(encoding="utf-8"))["topic_sources"] == {"llm_agents": "interest-note"}


def test_scout_status_records_topics_manifest_provenance(tmp_path: Path, monkeypatch: Any) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "source-candidates.jsonl"
    status = tmp_path / "status.json"
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_MODE", "paperwiki_kg")
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_PATH", str(tmp_path / "traveler-scout-topics.paperwiki.json"))
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_TOPICS_GENERATED_FROM", json.dumps({"base_topics": 4, "interests": 2}))
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_TOPICS_TRUST_POLICY", "trust-policy-1.0")

    create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, status_path=status)

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["topics_source_mode"] == "paperwiki_kg"
    assert payload["topics_source_path"].endswith("traveler-scout-topics.paperwiki.json")
    assert payload["topics_generated_from"] == {"base_topics": 4, "interests": 2}
    assert payload["topics_trust_policy"] == "trust-policy-1.0"


def test_scout_skips_existing_pending_topic(tmp_path: Path) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "scout-candidates.jsonl"

    first = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, dry_run=False)
    second = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, dry_run=False)

    rows = _jsonl(research)
    assert first["status"]["requests_created"] == 1
    assert second["status"]["requests_created"] == 0
    assert second["status"]["requests_skipped_existing"] == 1
    assert second["status"]["skipped_existing_topics"] == [topics[0].topic_id]
    assert len(rows) == 1


def test_scout_ignores_stale_live_test_rows_when_checking_pending_topics(tmp_path: Path) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "source-candidates.jsonl"
    research.write_text(
        json.dumps(
            {
                "request_id": "traveler_request_test",
                "status": "pending_deep_research",
                "topic": "LIVE TEST - 집현전 여행자 연결 검증",
                "requester_note": "safe to ignore",
                "discovery_mode": "autonomous_scout",
                "scout_topic_id": topics[0].topic_id,
                "candidate_queue_path": str(scout),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, dry_run=False)

    rows = _jsonl(research)
    assert result["status"]["requests_created"] == 1
    assert result["status"]["requests_skipped_existing"] == 0
    assert [row["request_id"] for row in rows if row.get("request_id") != "traveler_request_test"]
    assert rows[-1]["discovery_mode"] == "autonomous_scout"
    assert rows[-1]["scout_topic_id"] == topics[0].topic_id


def test_scout_requeues_stale_non_test_pending_topic(tmp_path: Path, monkeypatch: Any) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "source-candidates.jsonl"
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_SCOUT_STALE_PENDING_HOURS", "1")
    research.write_text(
        json.dumps(
            {
                "request_id": "traveler_request_stale",
                "status": "pending_deep_research",
                "topic": topics[0].query,
                "created_at": "2026-01-01T00:00:00Z",
                "discovery_mode": "autonomous_scout",
                "scout_topic_id": topics[0].topic_id,
                "candidate_queue_path": str(scout),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout, dry_run=False)

    rows = _jsonl(research)
    assert result["status"]["requests_created"] == 1
    assert result["status"]["requests_skipped_existing"] == 0
    assert result["status"]["stale_pending_topics"] == [topics[0].topic_id]
    assert len(rows) == 2
    assert rows[-1]["request_id"] != "traveler_request_stale"
    assert rows[-1]["scout_topic_id"] == topics[0].topic_id


def test_default_scout_queue_path_uses_env(monkeypatch: Any, tmp_path: Path) -> None:
    target = tmp_path / "custom-scout.jsonl"
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH", str(target))

    assert default_scout_queue_path() == target


def test_scout_can_write_autonomous_requests_to_canonical_source_queue(tmp_path: Path) -> None:
    topics = load_scout_topics(None)[:1]
    research = tmp_path / "research.jsonl"
    source_queue = tmp_path / "source-candidates.jsonl"

    result = create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=source_queue)

    rows = _jsonl(research)
    assert result["status"]["scout_queue_path"] == str(source_queue)
    assert rows[0]["candidate_queue_path"] == str(source_queue)
