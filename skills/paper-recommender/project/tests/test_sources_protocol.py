from __future__ import annotations

import asyncio
import dataclasses

import pytest

from paper_recommender.sources import (
    CandidateItem,
    SourceAdapter,
    SourceLimits,
    fetch_all_sources,
)


def test_candidate_item_is_hashable_and_frozen() -> None:
    item = CandidateItem(source="arxiv", title="Hello", authors=("a", "b"), tags=("ml",))
    assert hash(item) is not None
    assert item in {item}
    with pytest.raises(dataclasses.FrozenInstanceError):
        item.title = "X"  # type: ignore[misc]


def test_candidate_item_default_fetched_at_is_utc() -> None:
    item = CandidateItem(source="hn", title="x")
    assert item.fetched_at.tzinfo is not None


def test_source_limits_defaults_match_spec() -> None:
    lim = SourceLimits()
    assert lim.max_per_source == 50
    assert lim.year_from is None
    assert lim.timeout_sec == 30.0


class _FakeAdapter:
    def __init__(self, name: str, items: list[CandidateItem], should_raise: bool = False) -> None:
        self.name = name
        self._items = items
        self._raise = should_raise

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        if self._raise:
            raise RuntimeError(f"{self.name} kaboom")
        return list(self._items)


def test_fake_adapter_satisfies_protocol() -> None:
    a = _FakeAdapter("arxiv", [])
    assert isinstance(a, SourceAdapter)


def test_fetch_all_sources_returns_per_source_dict() -> None:
    a = _FakeAdapter("arxiv", [CandidateItem(source="arxiv", title="A1")])
    b = _FakeAdapter(
        "hackernews",
        [
            CandidateItem(source="hackernews", title="B1"),
            CandidateItem(source="hackernews", title="B2"),
        ],
    )
    out = asyncio.run(fetch_all_sources([a, b], ["topic"], SourceLimits()))
    assert set(out.keys()) == {"arxiv", "hackernews"}
    assert [it.title for it in out["arxiv"]] == ["A1"]
    assert [it.title for it in out["hackernews"]] == ["B1", "B2"]


def test_fetch_all_sources_isolates_adapter_failures() -> None:
    good = _FakeAdapter("arxiv", [CandidateItem(source="arxiv", title="ok")])
    bad = _FakeAdapter("hackernews", [], should_raise=True)
    out = asyncio.run(fetch_all_sources([good, bad], ["topic"], SourceLimits()))
    assert "arxiv" in out
    assert "hackernews" not in out
    assert len(out["arxiv"]) == 1


def test_fetch_all_sources_runs_adapters_concurrently() -> None:
    """Two adapters that each sleep N seconds should finish in ~N seconds total,
    not 2N. Generous tolerance to keep this resilient on loaded CI runners."""

    class _SlowAdapter:
        def __init__(self, name: str, delay: float) -> None:
            self.name = name
            self._delay = delay

        async def fetch(
            self,
            seed_topics: list[str],
            limits: SourceLimits,
        ) -> list[CandidateItem]:
            await asyncio.sleep(self._delay)
            return [CandidateItem(source=self.name, title=self.name)]

    import time

    a = _SlowAdapter("arxiv", 0.10)
    b = _SlowAdapter("hackernews", 0.10)
    start = time.monotonic()
    out = asyncio.run(fetch_all_sources([a, b], ["x"], SourceLimits()))
    elapsed = time.monotonic() - start
    assert set(out.keys()) == {"arxiv", "hackernews"}
    assert elapsed < 0.18, f"sources ran serially (elapsed={elapsed:.3f}s)"


def test_fetch_all_sources_empty_list_returns_empty_dict() -> None:
    out = asyncio.run(fetch_all_sources([], ["topic"], SourceLimits()))
    assert out == {}


def test_candidate_item_truncates_long_abstract() -> None:
    """A pathologically long abstract (e.g. full RSS body) is auto-truncated."""

    long = "x" * 5000
    item = CandidateItem(source="rss", title="t", abstract=long)
    assert item.abstract is not None
    assert len(item.abstract) <= CandidateItem.MAX_ABSTRACT_CHARS + 16
    assert item.abstract.endswith(" [...]")


def test_candidate_item_keeps_short_abstract_unchanged() -> None:
    item = CandidateItem(source="arxiv", title="t", abstract="short summary")
    assert item.abstract == "short summary"


def test_candidate_item_handles_none_abstract() -> None:
    item = CandidateItem(source="hn", title="t", abstract=None)
    assert item.abstract is None


def test_fetch_all_sources_kills_runaway_adapter(monkeypatch) -> None:
    """An adapter that ignores limits.timeout_sec must not block siblings."""

    import paper_recommender.sources as src_mod

    monkeypatch.setattr(src_mod, "_FETCH_TIMEOUT_GRACE_SEC", 0.1)

    class _Runaway:
        name = "runaway"

        async def fetch(
            self,
            seed_topics: list[str],
            limits: SourceLimits,
        ) -> list[CandidateItem]:
            await asyncio.sleep(2.0)
            return []

    class _Fast:
        name = "fast"

        async def fetch(
            self,
            seed_topics: list[str],
            limits: SourceLimits,
        ) -> list[CandidateItem]:
            return [CandidateItem(source="fast", title="ok")]

    limits = SourceLimits(timeout_sec=0.1)
    out = asyncio.run(fetch_all_sources([_Runaway(), _Fast()], ["x"], limits))
    assert "runaway" not in out
    assert "fast" in out
    assert [it.title for it in out["fast"]] == ["ok"]
