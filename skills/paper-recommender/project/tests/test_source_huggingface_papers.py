from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.huggingface_papers import HuggingFacePapersAdapter

# Pin "today" so date offsets are deterministic
_FIXED_TODAY = datetime(2026, 5, 2, tzinfo=timezone.utc)


def _make_adapter(handler, **kw) -> HuggingFacePapersAdapter:
    return HuggingFacePapersAdapter(
        _transport=httpx.MockTransport(handler),
        _today=_FIXED_TODAY,
        **kw,
    )


def _entry(arxiv_id: str, title: str, year: int = 2026, *, summary: str = "") -> dict:
    return {
        "paper": {
            "id": arxiv_id,
            "title": title,
            "summary": summary,
            "authors": [{"name": "Alice"}, {"name": "Bob"}],
            "publishedAt": f"{year}-01-15T00:00:00.000Z",
        },
        "upvotes": 10,
        "numComments": 2,
    }


def test_hf_happy_path() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_entry("2601.00001", "Awesome paper", year=2026)],
        )

    items = asyncio.run(
        _make_adapter(handler, days_back=1).fetch([], SourceLimits(max_per_source=10)),
    )
    assert len(items) == 1
    p = items[0]
    assert p.source == "huggingface_papers"
    assert p.title == "Awesome paper"
    assert p.arxiv_id == "2601.00001"
    assert p.url == "https://arxiv.org/abs/2601.00001"
    assert p.year == 2026
    assert p.authors == ("Alice", "Bob")
    assert p.score == 10.0


def test_hf_topic_filter_keeps_only_matching() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _entry("2601.00001", "transformer attention paper", summary="..."),
                _entry("2601.00002", "completely unrelated topic", summary="..."),
            ],
        )

    items = asyncio.run(
        _make_adapter(handler, days_back=1).fetch(["transformer"], SourceLimits()),
    )
    assert [it.arxiv_id for it in items] == ["2601.00001"]


def test_hf_topic_match_in_abstract() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_entry("2601.99999", "Generic title", summary="Discusses RAG architectures")],
        )

    items = asyncio.run(
        _make_adapter(handler, days_back=1).fetch(["rag"], SourceLimits()),
    )
    assert len(items) == 1


def test_hf_walks_back_days() -> None:
    seen_dates: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Pull the date param out of the URL
        seen_dates.append(str(request.url).split("date=")[-1])
        return httpx.Response(200, json=[])

    asyncio.run(
        _make_adapter(handler, days_back=3).fetch([], SourceLimits()),
    )
    assert seen_dates == ["2026-05-02", "2026-05-01", "2026-04-30"]


def test_hf_dedupes_by_arxiv_id_across_days() -> None:
    """Same paper can appear on multiple days; dedup by arxiv_id."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_entry("2601.00001", "same paper")])

    items = asyncio.run(
        _make_adapter(handler, days_back=3).fetch([], SourceLimits(max_per_source=10)),
    )
    assert len(items) == 1


def test_hf_per_day_failure_isolated() -> None:
    counter = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] == 1:
            return httpx.Response(503, text="bad")
        return httpx.Response(200, json=[_entry("2601.00001", "ok")])

    items = asyncio.run(
        _make_adapter(handler, days_back=2).fetch([], SourceLimits()),
    )
    assert [it.title for it in items] == ["ok"]


def test_hf_year_from_filters_old_papers() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _entry("2401.00001", "new", year=2024),
                _entry("2201.00001", "old", year=2022),
            ],
        )

    items = asyncio.run(
        _make_adapter(handler, days_back=1).fetch([], SourceLimits(year_from=2024)),
    )
    titles = {i.title for i in items}
    assert "new" in titles and "old" not in titles


def test_hf_invalid_days_back_rejected() -> None:
    import pytest

    with pytest.raises(ValueError):
        HuggingFacePapersAdapter(days_back=0)


def test_hf_max_per_source_caps_across_days() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[_entry(f"260{i}.00001", f"p{i}") for i in range(5)],
        )

    items = asyncio.run(
        _make_adapter(handler, days_back=3).fetch([], SourceLimits(max_per_source=4)),
    )
    assert len(items) == 4
