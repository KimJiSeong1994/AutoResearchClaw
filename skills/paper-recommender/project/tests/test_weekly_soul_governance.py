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



def test_weekly_markdown_reports_soul_axis_coverage_and_missing_axes(tmp_path) -> None:
    md = render_weekly_report(
        _settings(tmp_path),
        profile={"keywords": ["dynamic graph"]},
        soul_md="dynamic graph representation\ndiachronic semantics",
        user_id="researcher-1",
        queries=[
            {"axis": "dynamic graph", "query": "dynamic graph 2026", "rationale": "SOUL axis"},
            {"axis": "diachronic semantics", "query": "semantic change 2026", "rationale": "SOUL axis"},
        ],
        candidates=[
            {
                "paper_id": "p1",
                "title": "Dynamic graph paper",
                "year": 2026,
                "source": "arxiv",
                "_trend_axis": "dynamic graph",
                "_trend_query": "dynamic graph 2026",
            }
        ],
        report={"coverage_caveat": "limited", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    assert "## SOUL axis coverage" in md
    assert "- ✅ **dynamic graph:** covered by 1 candidate(s)." in md
    assert "- ⚠️ **diachronic semantics:** missing visible candidate evidence." in md


def test_weekly_raw_records_soul_axis_coverage(tmp_path) -> None:
    settings = _settings(tmp_path)
    target = write_weekly_artifacts(
        settings,
        profile={"keywords": ["dynamic graph"]},
        soul_md="dynamic graph representation",
        user_id="researcher-1",
        soul_card=None,
        soul_provenance={"source": "soul", "present": True, "fallback_used": False},
        queries=[
            {"axis": "dynamic graph", "query": "dynamic graph 2026", "rationale": "SOUL axis"},
            {"axis": "word embedding drift", "query": "dynamic word embedding", "rationale": "SOUL axis"},
        ],
        candidates=[
            {
                "paper_id": "p1",
                "title": "Dynamic graph paper",
                "year": 2026,
                "source": "arxiv",
                "_trend_axis": "dynamic graph",
                "_trend_query": "dynamic graph 2026",
            }
        ],
        report={"coverage_caveat": "limited", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    raw = json.loads((target / settings.weekly_report.raw_filename).read_text())
    assert raw["soul_axis_coverage"] == [
        {"axis": "dynamic graph", "candidate_count": 1, "covered": True},
        {"axis": "word embedding drift", "candidate_count": 0, "covered": False},
    ]

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



def test_weekly_markdown_renders_soul_axis_coverage_and_missing_axes(tmp_path) -> None:
    md = render_weekly_report(
        _settings(tmp_path),
        profile={"keywords": ["dynamic graph learning"]},
        soul_md="SOUL: diachronic semantics and graph recommendation",
        user_id="researcher-1",
        soul_card="Keywords: diachronic semantics; graph recommendation",
        soul_provenance={"source": "soul", "present": True, "fallback_used": False},
        queries=[
            {"axis": "diachronic semantic change", "query": "diachronic semantic change embedding 2026", "rationale": "SOUL axis"},
            {"axis": "graph recommendation", "query": "graph recommendation systems 2026", "rationale": "SOUL axis"},
        ],
        candidates=[
            {
                "paper_id": "p1",
                "title": "Temporal Meaning Shift in Embedding Spaces",
                "year": 2026,
                "source": "jh",
                "_trend_axis": "diachronic semantic change",
                "_trend_query": "diachronic semantic change embedding 2026",
            }
        ],
        report={"coverage_caveat": "limited", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    assert "## SOUL-axis coverage" in md
    assert "✅ **diachronic semantic change** — covered by 1 candidate(s)" in md
    assert "⚠️ **graph recommendation** — missing candidate evidence" in md
    assert "Missing axes to revisit: graph recommendation" in md


def test_weekly_raw_records_soul_axis_coverage(tmp_path) -> None:
    settings = _settings(tmp_path)
    target = write_weekly_artifacts(
        settings,
        profile={"keywords": ["dynamic graph learning"]},
        soul_md="SOUL: diachronic semantics",
        user_id="researcher-1",
        soul_card="Keywords: diachronic semantics",
        soul_provenance={"source": "soul", "present": True, "fallback_used": False},
        queries=[{"axis": "diachronic semantic change", "query": "diachronic semantic change", "rationale": "SOUL axis"}],
        candidates=[{"paper_id": "p1", "title": "Meaning Shift", "_trend_axis": "diachronic semantic change"}],
        report={"coverage_caveat": "", "clusters": []},
        run_iso="2026-05-04T00:00:00+00:00",
    )

    raw = json.loads((target / settings.weekly_report.raw_filename).read_text())
    assert raw["soul_axis_coverage"] == [
        {
            "axis": "diachronic semantic change",
            "candidate_count": 1,
            "status": "covered",
            "query": "diachronic semantic change",
            "example_title": "Meaning Shift",
        }
    ]

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


def test_fallback_queries_skip_explicit_provenance_and_korean_noise_labels() -> None:
    settings = SimpleNamespace(
        profile=SimpleNamespace(seed_topics=[]),
        weekly_report=SimpleNamespace(max_queries=10),
    )
    queries = fallback_trend_queries(
        settings,
        "\n".join(
            [
                "graph retrieval augmented generation",
                "provenance: compact card source",
                "last updated: 2026-05-04",
                "운영 메모: 브리핑 포맷 변경",
                "변경 로그: suppress generic AI product news",
                "temporal graph benchmark evaluation",
            ]
        ),
        {"keywords": []},
    )

    joined = "\n".join(q["query"] for q in queries)
    assert "graph retrieval augmented generation" in joined
    assert "temporal graph benchmark evaluation" in joined
    assert "provenance" not in joined
    assert "last updated" not in joined
    assert "운영" not in joined
    assert "변경" not in joined
