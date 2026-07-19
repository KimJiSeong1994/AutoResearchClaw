"""The scoring config must actually steer decisions, and never break them.

Two failure modes matter here and they pull in opposite directions:

- a config nothing reads (the extraction would be decoration), and
- a config that can take the traveler down when it is missing or hand-edited
  badly on the box.

So these tests assert both: overrides change real decisions, and every broken
shape falls back to the shipped defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from discord_openclaw_bridge.traveler_evidence import (
    DEFAULT_SCORING,
    ExtractedEvidence,
    FetchResult,
    decide_evidence,
    load_scoring,
    read_scoring_config,
)
from discord_openclaw_bridge.traveler_source_discovery import DEFAULT_STATIC_SOURCES, load_static_sources

REPO_CONFIG = Path(__file__).resolve().parents[4] / "runtime/traveler-scoring.json"


def write_config(tmp_path: Path, payload: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "scoring.json"
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_SCORING_PATH", str(path))
    return path


def accepted_score(topic: str = "") -> float:
    decision = decide_evidence(
        FetchResult(status="ok", url="https://example.com/a", body="<html>b</html>", http_status=200),
        ExtractedEvidence(extractor="html", title="A paper", matched_keywords=["rag"]),
        topic=topic,
    )
    assert decision.candidate_state == "accepted"
    return decision.confidence_score


def test_committed_config_matches_the_hardcoded_defaults() -> None:
    """The shipped file must be a no-op, or the extraction changed behavior."""
    payload = json.loads(REPO_CONFIG.read_text(encoding="utf-8"))
    assert payload["evidence_scoring"] == DEFAULT_SCORING["evidence_scoring"]
    assert payload["curated_static_override"]["confidence_score"] == DEFAULT_SCORING["curated_static_override"]["confidence_score"]
    assert payload["curated_static_override"]["source_types"] == DEFAULT_SCORING["curated_static_override"]["source_types"]
    assert [tuple(row) for row in payload["static_sources"]] == list(DEFAULT_STATIC_SOURCES)


def test_committed_config_is_the_one_resolved_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIPHYEONJEON_TRAVELER_SCORING_PATH", raising=False)
    assert read_scoring_config(), "default path resolved to nothing — the config would be silently ignored"


def test_scoring_override_changes_the_decision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert accepted_score() == 0.65
    write_config(tmp_path, {"evidence_scoring": {"base_confidence": 0.3}}, monkeypatch)
    assert accepted_score() == 0.35


def test_static_source_override_changes_the_portfolio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(
        tmp_path,
        {"static_sources": [["Only One", "https://example.com/feed", "article_hub", "A note."]]},
        monkeypatch,
    )
    assert load_static_sources() == (("Only One", "https://example.com/feed", "article_hub", "A note."),)


def test_curated_override_confidence_is_configurable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, {"curated_static_override": {"confidence_score": 0.11}}, monkeypatch)
    assert load_scoring()["curated_static_override"]["confidence_score"] == 0.11


BROKEN_CONFIGS = (
    ("not json at all", "{{{"),
    ("json but not an object", "[1, 2, 3]"),
    ("sections wrong type", '{"evidence_scoring": 5, "static_sources": "nope"}'),
    ("empty object", "{}"),
)


@pytest.mark.parametrize("label,raw", BROKEN_CONFIGS)
def test_broken_config_falls_back_to_defaults(label: str, raw: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, raw, monkeypatch)
    assert load_scoring()["evidence_scoring"] == DEFAULT_SCORING["evidence_scoring"], label
    assert load_static_sources() == DEFAULT_STATIC_SOURCES, label
    assert accepted_score() == 0.65, label


def test_missing_config_falls_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_SCORING_PATH", "/nonexistent/traveler-scoring.json")
    assert load_scoring()["evidence_scoring"] == DEFAULT_SCORING["evidence_scoring"]
    assert load_static_sources() == DEFAULT_STATIC_SOURCES
    assert accepted_score() == 0.65


def test_partial_config_only_overrides_the_keys_it_sets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, {"evidence_scoring": {"base_confidence": 0.4}}, monkeypatch)
    knobs = load_scoring()["evidence_scoring"]
    assert knobs["base_confidence"] == 0.4
    assert knobs["max_confidence"] == DEFAULT_SCORING["evidence_scoring"]["max_confidence"]
    assert knobs["keyword_bonus_cap"] == DEFAULT_SCORING["evidence_scoring"]["keyword_bonus_cap"]


def test_wrong_typed_values_are_ignored_rather_than_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(tmp_path, {"evidence_scoring": {"base_confidence": "high", "max_confidence": None}}, monkeypatch)
    knobs = load_scoring()["evidence_scoring"]
    assert knobs["base_confidence"] == DEFAULT_SCORING["evidence_scoring"]["base_confidence"]
    assert knobs["max_confidence"] == DEFAULT_SCORING["evidence_scoring"]["max_confidence"]


MALFORMED_SOURCE_ROWS = (
    ("too few cells", [["a", "https://x/y", "t"]]),
    ("empty cell", [["", "https://x/y", "t", "r"]]),
    ("non-https url", [["a", "http://x/y", "t", "r"]]),
    ("non-string cell", [["a", "https://x/y", "t", 5]]),
    ("row is not a list", ["nope"]),
)


@pytest.mark.parametrize("label,rows", MALFORMED_SOURCE_ROWS)
def test_malformed_source_rows_fall_back_wholesale(label: str, rows: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dropping every bad row would leave an empty portfolio; fall back instead."""
    write_config(tmp_path, {"static_sources": rows}, monkeypatch)
    assert load_static_sources() == DEFAULT_STATIC_SOURCES, label


def test_valid_rows_survive_alongside_dropped_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(
        tmp_path,
        {"static_sources": [["Good", "https://example.com/a", "article_hub", "ok"], ["Bad", "http://x", "t", "r"]]},
        monkeypatch,
    )
    assert load_static_sources() == (("Good", "https://example.com/a", "article_hub", "ok"),)


def test_override_source_types_come_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Narrowing source_types in config must actually stop the override.

    Without this, replacing the config read with an equivalent hardcoded set
    is invisible: the shipped values match, so only a config that *differs*
    proves the read is real.
    """
    import asyncio

    from discord_openclaw_bridge.traveler import TravelerResearchRequest, record_research_request
    from discord_openclaw_bridge.traveler_source_discovery import (
        StaticTechnicalSourceProvider,
        discover_sources,
    )

    write_config(tmp_path, {"curated_static_override": {"source_types": ["archive_page"]}}, monkeypatch)

    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "source-candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    record_research_request(
        TravelerResearchRequest(
            topic="LLM agents research engineering",
            min_sources_to_review=10,
            max_candidates=1,
            discovery_mode="autonomous_scout",
            scout_topic_id="llm_agents",
        ),
        queue_path=research,
        candidate_queue_path=candidates,
    )

    def fetcher(url: str) -> FetchResult:
        return FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=120,
            body="<html><head><title>Public recurring research updates</title></head></html>",
        )

    asyncio.run(
        discover_sources(
            research_queue_path=research,
            default_candidate_queue_path=candidates,
            providers=[StaticTechnicalSourceProvider()],
            deep_research=True,
            evidence_path=evidence,
            evidence_fetcher=fetcher,
        )
    )

    reasons = {
        json.loads(line)["decision"]["reason"]
        for line in evidence.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert "curated_static_source_surface_requires_review" not in reasons, (
        "override fired for a source_type the config excluded — the config read is not load-bearing"
    )
