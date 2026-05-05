from __future__ import annotations

import json
from types import SimpleNamespace

from paper_recommender.trend_queries import fallback_trend_queries
from paper_recommender.weekly import _compact_soul_card
from paper_recommender.weekly_obsidian import render_weekly_report, write_weekly_artifacts


def _settings(tmp_path):
    return SimpleNamespace(
        artifacts_root=tmp_path,
        weekly_report=SimpleNamespace(
            output_subdir_fmt="weekly/%G-W%V",
            note_filename="research-trends.md",
            raw_filename="raw.json",
            top_papers=5,
            max_queries=10,
        ),
        profile=SimpleNamespace(seed_topics=["seed graph topic"]),
        soul=SimpleNamespace(weekly_snapshot_mode="redacted", weekly_snapshot_max_chars=1200),
    )


def test_weekly_report_marks_profile_fallback_distinctly(tmp_path) -> None:
    md = render_weekly_report(
        _settings(tmp_path),
        profile={"interests": ["dynamic graph learning"]},
        soul_md="profile narrative fallback",
        user_id="researcher-1",
        soul_card="Interests: dynamic graph learning",
        soul_provenance={
            "source": "profile_narrative_fallback",
            "present": False,
            "fallback_used": True,
            "active_bytes": 26,
            "compact_card_bytes": 34,
            "active_sha256": "abc123",
        },
        queries=[],
        candidates=[],
        report={"coverage_caveat": "limited evidence", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    assert "report_type: weekly-profile-trends" in md
    assert 'soul_source: "profile_narrative_fallback"' in md
    assert "soul_fallback_used: true" in md
    assert "SOUL unavailable" in md
    assert "profile/narrative fallback was used" in md


def test_weekly_raw_records_soul_provenance_and_compact_card(tmp_path) -> None:
    settings = _settings(tmp_path)
    target = write_weekly_artifacts(
        settings,
        profile={"keywords": ["heterogeneous graph embedding"]},
        soul_md="full soul text",
        user_id="researcher-1",
        soul_card="Keywords: heterogeneous graph embedding",
        soul_provenance={
            "source": "soul",
            "present": True,
            "fallback_used": False,
            "active_bytes": 14,
            "active_sha256": "hash",
            "compact_card_bytes": 37,
        },
        queries=[],
        candidates=[],
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    raw = json.loads((target / settings.weekly_report.raw_filename).read_text())
    assert raw["soul_present"] is True
    assert raw["soul_source"] == "soul"
    assert raw["soul_fallback_used"] is False
    assert raw["soul_provenance"]["active_sha256"] == "hash"
    assert raw["soul_card"] == "Keywords: heterogeneous graph embedding"


def test_compact_soul_card_prefers_profile_sections_over_governance_noise() -> None:
    card = _compact_soul_card(
        "\n".join(
            [
                "# Research profile",
                "- Temporal graph neural networks",
                "- Graph retrieval augmented generation",
                "# Changelog",
                "- Added Discord briefing governance",
                "# Blind spots",
                "- suppress generic AI product news",
                "# Methodology preferences",
                "- Prefer benchmarked retrieval studies",
            ]
        ),
        {"keywords": ["heterogeneous graphs"]},
        limit=400,
    )

    assert card is not None
    assert "Keywords: heterogeneous graphs" in card
    assert "[Research profile]" in card
    assert "Temporal graph neural networks" in card
    assert "[Methodology preferences]" in card
    assert "benchmarked retrieval studies" in card
    assert "Discord briefing governance" not in card
    assert "suppress generic" not in card


def test_weekly_markdown_redacts_soul_snapshot_by_default(tmp_path) -> None:
    md = render_weekly_report(
        _settings(tmp_path),
        profile={"keywords": ["heterogeneous graph embedding"]},
        soul_md="private SOUL text token=abc123supersecret researcher@example.com",
        user_id="researcher-1",
        soul_card="Keywords: heterogeneous graph embedding",
        soul_provenance={
            "source": "soul",
            "present": True,
            "fallback_used": False,
            "active_bytes": 60,
            "active_sha256": "hash",
            "compact_card_bytes": 37,
        },
        queries=[],
        candidates=[],
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    assert "SOUL context snapshot omitted" in md
    assert "abc123supersecret" not in md
    assert "researcher@example.com" not in md


def test_weekly_markdown_can_include_sanitized_truncated_snapshot(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.soul.weekly_snapshot_mode = "truncated"
    settings.soul.weekly_snapshot_max_chars = 36

    md = render_weekly_report(
        settings,
        profile={"keywords": ["heterogeneous graph embedding"]},
        soul_md="token=abc123supersecret contact researcher@example.com with graph preferences",
        user_id="researcher-1",
        soul_card=None,
        soul_provenance={"source": "soul", "present": True, "fallback_used": False},
        queries=[],
        candidates=[],
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    assert "<details><summary>SOUL context snapshot</summary>" in md
    assert "REDACTED" in md
    assert "abc123supersecret" not in md
    assert "researcher@example.com" not in md
    assert "[truncated]" in md


def test_weekly_reading_queue_preserves_ranked_order_and_cap(tmp_path) -> None:
    settings = _settings(tmp_path)
    settings.weekly_report.top_papers = 2

    candidates = [
        {"paper_id": "best", "title": "Best ranked paper", "_trend_query": "dynamic graph ranking"},
        {"paper_id": "second", "title": "Second ranked paper", "_trend_query": "temporal graph methods"},
        {"paper_id": "third", "title": "Third capped paper", "_trend_query": "noise beyond cap"},
    ]

    md = render_weekly_report(
        settings,
        profile={"keywords": []},
        soul_md=None,
        user_id=None,
        queries=[],
        candidates=candidates,
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    assert "## Reading queue" in md
    first = md.index("1. **Best ranked paper**")
    second = md.index("2. **Second ranked paper**")
    assert first < second
    assert "Third capped paper" not in md

    target = write_weekly_artifacts(
        settings,
        profile={"keywords": []},
        soul_md=None,
        user_id=None,
        queries=[],
        candidates=candidates,
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )
    raw = json.loads((target / settings.weekly_report.raw_filename).read_text())
    assert [p["paper_id"] for p in raw["candidates"]] == ["best", "second"]


def test_weekly_reading_queue_defangs_noisy_query_labels(tmp_path) -> None:
    md = render_weekly_report(
        _settings(tmp_path),
        profile={"keywords": []},
        soul_md=None,
        user_id=None,
        queries=[],
        candidates=[
            {
                "paper_id": "p1",
                "title": "Ranked <paper> | with [[wikilink]]",
                "_trend_query": "graph RAG | <script>\n## Changelog [noise]",
            }
        ],
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    queue_line = next(line for line in md.splitlines() if line.startswith("1. **"))
    assert "Ranked paper \\| with wikilink" in queue_line
    assert "graph RAG \\| script ## Changelog noise" in queue_line
    assert "<script>" not in queue_line
    assert "[[" not in queue_line
    assert "]]" not in queue_line


def test_fallback_queries_skip_soul_governance_noise() -> None:
    settings = SimpleNamespace(
        profile=SimpleNamespace(seed_topics=[]),
        weekly_report=SimpleNamespace(max_queries=10),
    )
    queries = fallback_trend_queries(
        settings,
        "\n".join(
            [
                "dynamic graph representation learning",
                "## Changelog",
                "- 2026-05-04 changed briefing format",
                "- suppress generic AI product news",
                "- heterogeneous temporal graph neural networks",
            ]
        ),
        {"keywords": []},
    )
    joined = "\n".join(q["query"] for q in queries)
    assert "dynamic graph representation learning" in joined
    assert "heterogeneous temporal graph neural networks" in joined
    assert "Changelog" not in joined
    assert "suppress generic" not in joined
