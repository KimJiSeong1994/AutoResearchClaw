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


def test_default_scout_queue_path_uses_env(monkeypatch: Any, tmp_path: Path) -> None:
    target = tmp_path / "custom-scout.jsonl"
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH", str(target))

    assert default_scout_queue_path() == target
