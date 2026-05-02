from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from paper_recommender.clustering import Cluster
from paper_recommender.config import DeepBridgeSettings
from paper_recommender.deep_bridge import (
    DeepReport,
    cluster_dedup_key,
    cluster_topic_for_deep,
    run_deep_for_clusters,
)
from paper_recommender.sources import CandidateItem


# ─────────────── fixtures / helpers ───────────────


def _settings(tmp_path: Path, **kw) -> DeepBridgeSettings:
    """Build a DeepBridgeSettings pointing at tmp paths.

    A throwaway run-topic.sh is created so the existence check passes; the
    test's stub runner intercepts before the script is actually executed.
    """
    script = tmp_path / "run-topic.sh"
    script.write_text("#!/usr/bin/env bash\necho fake\n")
    script.chmod(0o755)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(exist_ok=True)
    defaults = {
        "enabled": True,
        "concurrency": 1,
        "timeout_sec": 60,
        "mode": "full-auto",
        "run_topic_script": str(script),
        "artifacts_root": str(artifacts),
    }
    defaults.update(kw)
    return DeepBridgeSettings(**defaults)


def _cluster(cid: int = 0, *, label: str = "Sample Topic", n: int = 3) -> Cluster:
    return Cluster(
        id=cid,
        items=[CandidateItem(source="t", title=f"item-{cid}-{i}") for i in range(n)],
        centroid=[],
        centroid_keywords=["alpha", "beta"],
        label=label,
    )


def _make_artifact(
    artifacts_root: Path,
    name: str,
    *,
    last_stage: int = 9,
    last_name: str = "EXPERIMENT_DESIGN",
    synthesis: str | None = "Synthesized findings.",
    extra_md: dict[str, str] | None = None,
) -> Path:
    rc = artifacts_root / name
    rc.mkdir(parents=True, exist_ok=True)
    (rc / "checkpoint.json").write_text(json.dumps({
        "last_completed_stage": last_stage,
        "last_completed_name": last_name,
        "run_id": name,
        "timestamp": "2026-05-02T12:00:00+00:00",
    }))
    if synthesis is not None:
        (rc / "stage-07").mkdir(exist_ok=True)
        (rc / "stage-07" / "synthesis.md").write_text(synthesis)
    for rel, content in (extra_md or {}).items():
        p = rc / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return rc


# ─────────────── topic + dedup helpers ───────────────


def test_cluster_topic_prefers_label() -> None:
    c = _cluster(label="Transformer attention mechanisms")
    assert cluster_topic_for_deep(c) == "Transformer attention mechanisms"


def test_cluster_topic_falls_back_to_keywords() -> None:
    c = Cluster(id=1, items=[], centroid=[], centroid_keywords=["transformer", "attention"], label="")
    assert cluster_topic_for_deep(c) == "transformer, attention"


def test_cluster_dedup_key_normalizes() -> None:
    a = _cluster(label="Show HN: My Tool")
    b = _cluster(label="show hn: my tool")
    assert cluster_dedup_key(a) == cluster_dedup_key(b)


# ─────────────── happy-path ───────────────


def test_successful_run_produces_deep_report(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def stub_runner(args: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        _make_artifact(Path(settings.artifacts_root), "rc-20260502-080000-abc123")
        return 0, b"ok", b""

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    assert len(reports) == 1
    r = reports[0]
    assert r.success is True
    assert r.exit_code == 0
    assert r.last_completed_stage == 9
    assert r.last_completed_name == "EXPERIMENT_DESIGN"
    assert r.artifact_path is not None and r.artifact_path.name.startswith("rc-")
    assert r.main_report_path is not None and r.main_report_path.name == "synthesis.md"
    assert "Synthesized findings" in r.markdown_excerpt
    assert r.error == ""
    assert r.wall_clock_sec >= 0


def test_disabled_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path, enabled=False)

    async def stub_runner(*args, **kw):
        raise AssertionError("should not run")

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    assert reports == []


def test_no_clusters_returns_empty(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def stub_runner(*args, **kw):
        raise AssertionError("should not run")

    reports = asyncio.run(run_deep_for_clusters([], settings, runner=stub_runner))
    assert reports == []


# ─────────────── failure modes ───────────────


def test_missing_script_marks_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.run_topic_script = str(tmp_path / "does-not-exist.sh")

    async def stub_runner(*args, **kw):
        raise AssertionError("should never run")

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    assert reports[0].success is False
    assert "not found" in reports[0].error


def test_no_artifact_dir_produced_marks_failed(tmp_path: Path) -> None:
    """The CLI exits 0 (config-not-found behavior) without producing an rc-* dir."""
    settings = _settings(tmp_path)

    async def stub_runner(args, timeout):
        return 0, b"", b"Error: config file not found"

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    r = reports[0]
    assert r.success is False
    assert r.exit_code == 0
    assert "no artifact" in r.error
    assert "config file not found" in r.error


def test_partial_run_below_min_stage_marks_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def stub_runner(args, timeout):
        _make_artifact(
            Path(settings.artifacts_root),
            "rc-20260502-090000-xyz",
            last_stage=4,
            last_name="LITERATURE_REVIEW",
            synthesis=None,
        )
        return 0, b"", b""

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    r = reports[0]
    assert r.success is False
    assert r.last_completed_stage == 4
    assert "below required" in r.error


def test_timeout_marks_failed(tmp_path: Path) -> None:
    settings = _settings(tmp_path, timeout_sec=1)

    async def stub_runner(args, timeout):
        raise asyncio.TimeoutError()

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    r = reports[0]
    assert r.success is False
    assert r.exit_code == -3
    assert "timeout" in r.error.lower()


def test_continue_on_failure(tmp_path: Path) -> None:
    """A failed topic must NOT prevent later topics from running."""
    settings = _settings(tmp_path)
    counter = {"n": 0}

    async def stub_runner(args, timeout):
        counter["n"] += 1
        if counter["n"] == 1:
            return 0, b"", b"oops"
        _make_artifact(
            Path(settings.artifacts_root),
            f"rc-20260502-100000-aa{counter['n']}",
        )
        return 0, b"ok", b""

    reports = asyncio.run(
        run_deep_for_clusters(
            [_cluster(0), _cluster(1), _cluster(2)],
            settings,
            runner=stub_runner,
        )
    )
    assert len(reports) == 3
    assert [r.success for r in reports] == [False, True, True]


# ─────────────── on_progress ───────────────


def test_on_progress_called_per_cluster(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    counter = {"n": 0}

    async def stub_runner(args, timeout):
        counter["n"] += 1
        _make_artifact(
            Path(settings.artifacts_root),
            f"rc-20260502-110000-b{counter['n']}",
        )
        return 0, b"ok", b""

    seen: list[tuple[int, bool]] = []

    def on_progress(idx: int, report: DeepReport) -> None:
        seen.append((idx, report.success))

    asyncio.run(
        run_deep_for_clusters(
            [_cluster(0), _cluster(1)],
            settings,
            runner=stub_runner,
            on_progress=on_progress,
        )
    )
    assert seen == [(0, True), (1, True)]


def test_on_progress_exception_does_not_break_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    counter = {"n": 0}

    async def stub_runner(args, timeout):
        counter["n"] += 1
        _make_artifact(
            Path(settings.artifacts_root),
            f"rc-20260502-120000-c{counter['n']}",
        )
        return 0, b"ok", b""

    def bad_progress(idx: int, report: DeepReport) -> None:
        raise RuntimeError("test")

    reports = asyncio.run(
        run_deep_for_clusters(
            [_cluster(0), _cluster(1)],
            settings,
            runner=stub_runner,
            on_progress=bad_progress,
        )
    )
    assert all(r.success for r in reports)


# ─────────────── excerpt extraction fallback ───────────────


def test_excerpt_falls_back_to_hypotheses_when_synthesis_missing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def stub_runner(args, timeout):
        _make_artifact(
            Path(settings.artifacts_root),
            "rc-20260502-130000-d1",
            synthesis=None,  # NO synthesis.md
            extra_md={"stage-08/hypotheses.md": "Hypothesis content."},
        )
        return 0, b"", b""

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    r = reports[0]
    assert r.success is True
    assert r.main_report_path is not None
    assert r.main_report_path.name == "hypotheses.md"
    assert "Hypothesis content" in r.markdown_excerpt


def test_excerpt_glob_fallback_when_no_canonical_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    async def stub_runner(args, timeout):
        _make_artifact(
            Path(settings.artifacts_root),
            "rc-20260502-140000-e1",
            synthesis=None,
            extra_md={"stage-06/cards/some_paper.md": "Card content."},
        )
        return 0, b"", b""

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    r = reports[0]
    assert r.success is True
    assert r.main_report_path is not None
    assert r.main_report_path.name == "some_paper.md"
    assert "Card content" in r.markdown_excerpt


def test_excerpt_truncated_at_max_chars(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    huge = "x" * 10_000

    async def stub_runner(args, timeout):
        _make_artifact(
            Path(settings.artifacts_root),
            "rc-20260502-150000-f1",
            synthesis=huge,
        )
        return 0, b"", b""

    reports = asyncio.run(
        run_deep_for_clusters([_cluster()], settings, runner=stub_runner)
    )
    assert len(reports[0].markdown_excerpt) <= 3000


# ─────────────── concurrency knob ───────────────


def test_concurrency_one_serializes_runs(tmp_path: Path) -> None:
    """concurrency=1 means at most one runner call active at a time."""
    settings = _settings(tmp_path)
    in_flight = {"now": 0, "max": 0}
    counter = {"n": 0}

    async def stub_runner(args, timeout):
        in_flight["now"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["now"])
        await asyncio.sleep(0.01)
        counter["n"] += 1
        _make_artifact(
            Path(settings.artifacts_root),
            f"rc-20260502-160000-g{counter['n']}",
        )
        in_flight["now"] -= 1
        return 0, b"", b""

    asyncio.run(
        run_deep_for_clusters(
            [_cluster(0), _cluster(1), _cluster(2)],
            settings,
            runner=stub_runner,
        )
    )
    assert in_flight["max"] == 1
