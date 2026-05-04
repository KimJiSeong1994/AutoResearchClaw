from __future__ import annotations

import json
from types import SimpleNamespace

from paper_recommender.trend_queries import fallback_trend_queries
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
