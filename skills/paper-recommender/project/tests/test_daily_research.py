"""Integration test for the daily-research orchestrator.

Stubs every external dependency (jiphyeonjeon, embedding, LLM, deep bridge)
to verify the wiring: source merge → cluster → select → skip-filter → deep
→ note write. Real component behavior is covered by their own tests.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from paper_recommender import daily_research as dr_mod
from paper_recommender.clustering import Cluster, ClusterResult
from paper_recommender.daily_research import RunResult, run_daily_research
from paper_recommender.deep_bridge import DeepReport
from paper_recommender.sources import CandidateItem, SourceLimits


def _write_config(tmp_path: Path) -> Path:
    cfg = {
        "jiphyeonjeon": {
            "base_url": "https://jiphy.test",
            "token_env": "JIPHY_TEST_TOKEN",
            "timeout_sec": 10,
        },
        "openclaw": {
            "base_url": "http://localhost/v1",
            "token_env": "OPENCLAW_TEST_TOKEN",
            "primary_model": "test-model",
            "fallback_model": "test-fallback",
            "timeout_sec": 10,
        },
        "profile": {
            "cache_ttl_days": 7,
            "seed_topics": ["transformer", "graph neural networks"],
            "max_bookmarks_for_profile": 10,
            "narrative_enabled": True,
        },
        "candidates": {
            "per_keyword": 5,
            "related_per_bookmark": 2,
            "related_from_top_n_bookmarks": 1,
            "year_start": None,
            "year_end": None,
            "total_cap": 20,
        },
        "rerank": {
            "batch_size": 5,
            "top_k": 5,
            "min_score": 3.0,
            "temperature": 0.2,
        },
        "seen": {"cooldown_days": 30},
        "output": {
            "artifacts_dir": "artifacts",
            "daily_subdir_fmt": "%Y-%m-%d",
            "note_filename": "recommendations.md",
            "raw_filename": "raw.json",
        },
        "daily_research": {
            "sources": {"enabled": ["arxiv"], "max_per_source": 10},
            "cluster": {"max_clusters": 2},
            "deep": {
                "enabled": True,
                "concurrency": 1,
                "timeout_sec": 60,
                "run_topic_script": str(tmp_path / "run-topic.sh"),
                "artifacts_root": str(tmp_path / "rc-artifacts"),
            },
            "auth": {
                "base_url": "https://jiphy.test",
                "username_env": "STUB_USER",
                "password_env": "STUB_PW",
                "timeout_sec": 5.0,
            },
            "deep_seen_cooldown_days": 7,
        },
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg))
    return config_path


# ─────────────── stub dependencies ───────────────


class _StubProvider:
    async def get_token(self) -> str:
        return "STUB_TOKEN"

    def invalidate(self) -> None:
        pass


class _StubClient:
    """Stub of JiphyClient.list_bookmarks that returns canned data."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def list_bookmarks(self) -> list[dict]:
        return [
            {"topic": "diffusion models", "title": "DM paper"},
            {"topic": "transformer", "title": "T paper"},  # dup with seed_topics
        ]

    async def search(self, *args, **kw) -> list[dict]:
        return []


class _StubArxivAdapter:
    name = "arxiv"

    async def fetch(self, seed_topics, limits):
        return [
            CandidateItem(source="arxiv", title="Paper One", arxiv_id="2401.0001"),
            CandidateItem(source="arxiv", title="Paper Two", arxiv_id="2401.0002"),
            CandidateItem(source="arxiv", title="Paper Three", arxiv_id="2401.0003"),
            CandidateItem(source="arxiv", title="Paper Four", arxiv_id="2401.0004"),
        ]


class _StubEmbedClient:
    """Stub embedding client returning two clear clusters: even/odd indices."""

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Even indices → cluster A (1, 0), odd → cluster B (0, 1)
        return [[1.0, 0.0] if i % 2 == 0 else [0.0, 1.0] for i, _ in enumerate(texts)]


class _StubLLM:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def chat_json(self, messages):
        # Rank both clusters
        return {
            "ranking": [
                {"id": 0, "rank": 1, "label": "Even cluster", "summary": "even items"},
                {"id": 1, "rank": 2, "label": "Odd cluster", "summary": "odd items"},
            ]
        }


# ─────────────── tests ───────────────


def _factories(deep_runner=None):
    """Build the test-seam factories. Returns dict to splat into run_daily_research."""

    def token_factory(auth):
        return _StubProvider()

    def client_factory(jiphy_settings, provider):
        return _StubClient()

    def adapter_factory(enabled, jiphy_client):
        return [_StubArxivAdapter()]

    def embed_factory(settings):
        return _StubEmbedClient()

    def llm_factory(openclaw_settings):
        return _StubLLM()

    return {
        "_token_provider_factory": token_factory,
        "_client_factory": client_factory,
        "_adapter_factory": adapter_factory,
        "_embed_client_factory": embed_factory,
        "_llm_factory": llm_factory,
    }


def test_full_pipeline_dry_run_produces_note(tmp_path: Path, monkeypatch) -> None:
    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "static-token")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "static-openclaw")

    result = asyncio.run(
        run_daily_research(
            config,
            dry_run=True,
            **_factories(),
        )
    )

    assert isinstance(result, RunResult)
    assert result.candidate_count == 4
    assert result.source_stats == {"arxiv": 4}
    assert result.cluster_count == 2
    # Dry run should not write files
    assert result.paths_written == []
    assert "Daily Research" in result.note_markdown


def test_full_pipeline_writes_artifacts_when_not_dry_run(tmp_path: Path, monkeypatch) -> None:
    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "static-token")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "static-openclaw")

    # Stub deep_bridge so we don't actually try to run subprocess
    async def stub_deep(clusters, settings, **kw):
        return [
            DeepReport(
                cluster_id=c.id, topic=f"Topic {c.id}", success=True,
                exit_code=0, artifact_path=Path("/tmp/fake"),
                last_completed_stage=9, last_completed_name="EXPERIMENT_DESIGN",
                main_report_path=Path("/tmp/fake/stage-07/synthesis.md"),
                markdown_excerpt=f"Synthesis for cluster {c.id}",
                wall_clock_sec=1500.0,
            )
            for c in clusters
        ]

    monkeypatch.setattr(dr_mod, "run_deep_for_clusters", stub_deep)

    fixed_now = datetime(2026, 5, 2, 7, 0, 0, tzinfo=timezone.utc)
    result = asyncio.run(
        run_daily_research(
            config,
            dry_run=False,
            _now=lambda: fixed_now,
            **_factories(),
        )
    )

    # Two paths written: note + raw json
    assert len(result.paths_written) == 2
    note_path = next(p for p in result.paths_written if p.suffix == ".md")
    raw_path = next(p for p in result.paths_written if p.suffix == ".json")
    assert note_path.exists()
    assert raw_path.exists()
    assert note_path.parent.name == "2026-05-02"

    note = note_path.read_text(encoding="utf-8")
    assert "# Daily Research — 2026-05-02" in note
    assert "Synthesis for cluster" in note

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw["candidate_count"] == 4
    assert raw["deep_success_count"] == 2


def test_pipeline_skips_deep_seen_clusters(tmp_path: Path, monkeypatch) -> None:
    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "static-token")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "static-openclaw")

    # Pre-record one cluster as deep-seen
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    from paper_recommender.state import StateStore
    from paper_recommender.sources._util import normalize_title_for_dedup
    StateStore(state_dir).record_deep_seen([normalize_title_for_dedup("Even cluster")])

    seen_deep_calls: list[list[Cluster]] = []

    async def stub_deep(clusters, settings, **kw):
        seen_deep_calls.append(list(clusters))
        return [
            DeepReport(
                cluster_id=c.id, topic=f"Topic {c.id}", success=True,
                exit_code=0, artifact_path=None,
                last_completed_stage=9, last_completed_name="EXPERIMENT_DESIGN",
                main_report_path=None, markdown_excerpt="", wall_clock_sec=1.0,
            )
            for c in clusters
        ]

    monkeypatch.setattr(dr_mod, "run_deep_for_clusters", stub_deep)

    result = asyncio.run(
        run_daily_research(config, dry_run=False, **_factories())
    )
    # Only one cluster should be sent to deep (the un-seen one)
    assert len(seen_deep_calls) == 1
    assert len(seen_deep_calls[0]) == 1
    note = result.note_markdown
    assert "Skipped" in note
    assert "Even cluster" in note  # the skipped one


def test_pipeline_handles_empty_sources_gracefully(tmp_path: Path, monkeypatch) -> None:
    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "static-token")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "static-openclaw")

    class _EmptyAdapter:
        name = "arxiv"

        async def fetch(self, seed_topics, limits):
            return []

    factories = _factories()
    factories["_adapter_factory"] = lambda enabled, jh: [_EmptyAdapter()]

    result = asyncio.run(
        run_daily_research(config, dry_run=False, **factories)
    )
    assert result.candidate_count == 0
    assert result.cluster_count == 0
    # Still writes a note (empty but valid)
    assert any(p.suffix == ".md" for p in result.paths_written)


def test_pipeline_uses_fallback_when_embedding_fails(tmp_path: Path, monkeypatch) -> None:
    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "static-token")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "static-openclaw")

    class _BrokenEmbedClient:
        async def embed_batch(self, texts):
            raise RuntimeError("embedding service down")

    factories = _factories()
    factories["_embed_client_factory"] = lambda s: _BrokenEmbedClient()

    # Deep bridge should NOT be called when used_fallback=True
    deep_called = {"yes": False}

    async def stub_deep(*args, **kw):
        deep_called["yes"] = True
        return []

    monkeypatch.setattr(dr_mod, "run_deep_for_clusters", stub_deep)

    result = asyncio.run(
        run_daily_research(config, dry_run=False, **factories)
    )
    assert result.used_fallback is True
    assert deep_called["yes"] is False
    note = result.note_markdown
    assert "fallback" in note.lower() or "Embedding" in note


def test_missing_daily_research_section_raises(tmp_path: Path, monkeypatch) -> None:
    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "x")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "x")
    # Strip daily_research section
    cfg = yaml.safe_load(config.read_text())
    cfg.pop("daily_research")
    config.write_text(yaml.safe_dump(cfg))

    with pytest.raises(RuntimeError, match="daily_research"):
        asyncio.run(run_daily_research(config, dry_run=True, **_factories()))


def test_seed_topics_dedupe_bookmarks_and_explicit(tmp_path: Path, monkeypatch) -> None:
    """Bookmark 'transformer' should be deduped against seed_topics 'transformer'."""

    config = _write_config(tmp_path)
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "x")
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "x")

    captured_topics: list[list[str]] = []

    class _CapturingAdapter:
        name = "arxiv"

        async def fetch(self, seed_topics, limits):
            captured_topics.append(list(seed_topics))
            return []

    factories = _factories()
    factories["_adapter_factory"] = lambda enabled, jh: [_CapturingAdapter()]

    asyncio.run(run_daily_research(config, dry_run=True, **factories))

    seeds = captured_topics[0]
    # Each topic should appear at most once after normalization
    from paper_recommender.sources._util import normalize_title_for_dedup
    keys = [normalize_title_for_dedup(t) for t in seeds]
    assert len(keys) == len(set(keys))
    # Should contain bookmarks (diffusion, transformer) + explicit (graph...)
    assert "diffusion models" in seeds
    assert "graph neural networks" in seeds
