from __future__ import annotations

from pathlib import Path

from paper_recommender.clustering import Cluster
from paper_recommender.daily_note import SkippedCluster, compose_daily_note
from paper_recommender.deep_bridge import DeepReport
from paper_recommender.sources import CandidateItem


def _cluster(cid: int, *, label: str = "", n: int = 3, kw: list[str] | None = None) -> Cluster:
    return Cluster(
        id=cid,
        items=[CandidateItem(source="t", title=f"item-{cid}-{i}") for i in range(n)],
        centroid=[],
        centroid_keywords=kw or [f"kw{cid}"],
        label=label,
        summary=f"summary for {label or cid}",
    )


def _report(cid: int, *, success: bool = True, excerpt: str = "Synthesis content.", error: str = "") -> DeepReport:
    return DeepReport(
        cluster_id=cid,
        topic=f"Topic {cid}",
        success=success,
        exit_code=0 if success else 1,
        artifact_path=Path(f"/tmp/rc-2026-{cid:02d}"),
        last_completed_stage=9 if success else 4,
        last_completed_name="EXPERIMENT_DESIGN" if success else "LITERATURE_REVIEW",
        main_report_path=Path(f"/tmp/rc-2026-{cid:02d}/stage-07/synthesis.md") if success else None,
        markdown_excerpt=excerpt if success else "",
        wall_clock_sec=1234.0,
        error=error,
    )


def test_note_has_required_sections() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"arxiv": 10, "hackernews": 5},
        candidate_count=15,
        clusters=[_cluster(0, label="Transformer attention"), _cluster(1, label="Graph NN")],
        deep_reports=[_report(0)],
    )
    assert "---\ndate: \"2026-05-02\"" in md
    assert "tags:\n  - daily-research" in md
    assert "# Daily Research — 2026-05-02" in md
    assert "## Sources" in md
    assert "| arxiv | 10 |" in md
    assert "| **Total** | **15** |" in md
    assert "## Clusters (2)" in md
    assert "## Deep Reports" in md
    assert "Synthesis content." in md
    assert "Pipeline wall-clock" in md


def test_skipped_section_appears_with_reason() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"arxiv": 10},
        candidate_count=10,
        clusters=[_cluster(0, label="kept")],
        deep_reports=[_report(0)],
        skipped=[SkippedCluster(_cluster(99, label="seen-yesterday"), "deep-seen within 7 days")],
    )
    assert "## Skipped (deep-seen recently)" in md
    assert "~~seen-yesterday~~" in md
    assert "deep-seen within 7 days" in md


def test_fallback_warning_present() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"arxiv": 5},
        candidate_count=5,
        clusters=[_cluster(0, label="lone")],
        deep_reports=[],
        used_fallback=True,
    )
    assert "Embedding fallback active" in md
    assert "⚠️" in md or "Warning" in md.lower() or "fallback" in md.lower()


def test_failed_deep_report_renders_clearly() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"arxiv": 5},
        candidate_count=5,
        clusters=[_cluster(0, label="A")],
        deep_reports=[_report(0, success=False, error="run-topic.sh exited 1")],
    )
    assert "Failed" in md
    assert "run-topic.sh exited 1" in md


def test_safe_escaping_defangs_pipes_and_obsidian_links() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"a|b": 3},
        candidate_count=3,
        clusters=[_cluster(0, label="Title with [[link]] | pipe")],
        deep_reports=[],
    )
    # Pipe inside source name and label is escaped to avoid breaking the table / bold.
    assert "a\\|b" in md
    assert "[[link]]" not in md
    assert "[ [link] ]" in md
    assert "\\|" in md


def test_empty_inputs_still_produce_valid_note() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={},
        candidate_count=0,
        clusters=[],
        deep_reports=[],
    )
    assert "# Daily Research — 2026-05-02" in md
    assert "No source data" in md
    assert "No clusters formed" in md


def test_multiple_deep_reports_each_get_section() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"arxiv": 30},
        candidate_count=30,
        clusters=[_cluster(0, label="A"), _cluster(1, label="B"), _cluster(2, label="C")],
        deep_reports=[_report(0), _report(1), _report(2)],
    )
    # H3 deep-report sections
    assert md.count("### Topic 0") == 1
    assert md.count("### Topic 1") == 1
    assert md.count("### Topic 2") == 1
    assert "daily-research/topic-0" in md
    assert "daily-research/topic-1" in md
    assert "daily-research/topic-2" in md


def test_footer_summary_counts_correctly() -> None:
    md = compose_daily_note(
        run_iso="2026-05-02T07:00:00+00:00",
        source_stats={"arxiv": 10},
        candidate_count=10,
        clusters=[_cluster(0, label="A")],
        deep_reports=[_report(0), _report(1, success=False, error="boom")],
        wall_clock_sec=4321.0,
    )
    assert "deep success: 1/2" in md
    assert "wall-clock: 4321s" in md
    assert "candidates: 10" in md
