from __future__ import annotations

import asyncio
from typing import Any

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.jiphyeonjeon import JiphyeonjeonSourceAdapter


class _StubJiphyClient:
    """Stub matching JiphyClient.search() signature."""

    def __init__(self, by_query: dict[str, list[dict[str, Any]]] | None = None,
                 raise_for: set[str] | None = None) -> None:
        self._by_query = by_query or {}
        self._raise_for = raise_for or set()
        self.calls: list[tuple[str, int, int | None]] = []

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_start: int | None = None,
        year_end: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query, max_results, year_start))
        if query in self._raise_for:
            raise RuntimeError(f"boom on {query}")
        return list(self._by_query.get(query, []))


def test_jiphy_adapter_happy_path() -> None:
    client = _StubJiphyClient(by_query={
        "transformer": [
            {
                "title": "Attention is all you need",
                "abstract": "We propose a new architecture.",
                "authors": ["Vaswani", "Shazeer"],
                "year": 2017,
                "venue": "NeurIPS",
                "arxiv_id": "1706.03762",
                "doi": "10.1/x",
                "score": 0.92,
                "url": "https://arxiv.org/abs/1706.03762",
            }
        ],
    })

    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    items = asyncio.run(adapter.fetch(["transformer"], SourceLimits(max_per_source=5)))

    assert len(items) == 1
    item = items[0]
    assert item.source == "jiphyeonjeon"
    assert item.title == "Attention is all you need"
    assert item.arxiv_id == "1706.03762"
    assert item.doi == "10.1/x"
    assert item.year == 2017
    assert item.authors == ("Vaswani", "Shazeer")
    assert item.score == 0.92


def test_jiphy_adapter_per_topic_failure_isolated() -> None:
    client = _StubJiphyClient(
        by_query={"good": [{"title": "ok paper", "year": 2024}]},
        raise_for={"bad"},
    )
    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    items = asyncio.run(adapter.fetch(["bad", "good"], SourceLimits(max_per_source=5)))
    assert [it.title for it in items] == ["ok paper"]


def test_jiphy_adapter_dedupes_by_arxiv_id() -> None:
    client = _StubJiphyClient(by_query={
        "a": [{"title": "T1", "arxiv_id": "1234.5678"}],
        "b": [{"title": "T1 (dup)", "arxiv_id": "1234.5678"}],
    })
    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    items = asyncio.run(adapter.fetch(["a", "b"], SourceLimits(max_per_source=5)))
    assert len(items) == 1


def test_jiphy_adapter_passes_year_from_to_search() -> None:
    client = _StubJiphyClient(by_query={"x": []})
    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    asyncio.run(adapter.fetch(["x"], SourceLimits(year_from=2022)))
    assert client.calls[0][2] == 2022


def test_jiphy_adapter_distributes_max_across_topics() -> None:
    client = _StubJiphyClient(by_query={"a": [], "b": [], "c": []})
    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    asyncio.run(adapter.fetch(["a", "b", "c"], SourceLimits(max_per_source=9)))
    # 9 / 3 topics = 3 per topic
    assert all(call[1] == 3 for call in client.calls)


def test_jiphy_adapter_empty_topics_returns_empty() -> None:
    client = _StubJiphyClient()
    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    out = asyncio.run(adapter.fetch([], SourceLimits()))
    assert out == []
    assert client.calls == []


def test_jiphy_adapter_skips_papers_without_title() -> None:
    client = _StubJiphyClient(by_query={"x": [{"abstract": "no title"}, {"title": "ok"}]})
    adapter = JiphyeonjeonSourceAdapter(client)  # type: ignore[arg-type]
    items = asyncio.run(adapter.fetch(["x"], SourceLimits()))
    assert [it.title for it in items] == ["ok"]
