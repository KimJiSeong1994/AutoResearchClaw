from __future__ import annotations

import asyncio

import httpx

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.hackernews import HackerNewsAdapter


def _make_adapter(handler, **kw) -> HackerNewsAdapter:
    return HackerNewsAdapter(_transport=httpx.MockTransport(handler), **kw)


def test_hn_happy_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "hn.algolia.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "objectID": "100",
                        "title": "Show HN: My LLM Tool",
                        "url": "https://example.test/tool",
                        "author": "alice",
                        "points": 142,
                        "created_at": "2024-03-01T00:00:00.000Z",
                    },
                ],
                "nbPages": 1,
            },
        )

    items = asyncio.run(_make_adapter(handler).fetch(["llm"], SourceLimits()))
    assert len(items) == 1
    item = items[0]
    assert item.source == "hackernews"
    assert item.title == "Show HN: My LLM Tool"
    assert item.url == "https://example.test/tool"
    assert item.year == 2024
    assert item.authors == ("alice",)
    assert item.score == 142.0
    assert item.venue == "Hacker News"


def test_hn_falls_back_to_item_url_when_no_external_url() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "hits": [
                    {
                        "objectID": "999",
                        "title": "Ask HN: question",
                        "url": None,
                        "author": "bob",
                        "points": 3,
                        "created_at": "2024-01-01T00:00:00.000Z",
                    },
                ],
            },
        )

    items = asyncio.run(_make_adapter(handler).fetch(["q"], SourceLimits()))
    assert items[0].url == "https://news.ycombinator.com/item?id=999"


def test_hn_dedupes_normalized_titles_across_topics() -> None:
    """'Show HN: My Tool' from topic A and 'show hn: my tool' from topic B
    should be treated as the same item even with different objectIDs."""

    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] == 1:
            return httpx.Response(
                200,
                json={"hits": [{
                    "objectID": "1",
                    "title": "Show HN: My Tool",
                    "url": "https://x.test/1",
                    "points": 10,
                    "created_at": "2024-01-01T00:00:00.000Z",
                }]},
            )
        return httpx.Response(
            200,
            json={"hits": [{
                "objectID": "2",
                "title": "show hn: my tool",
                "url": "https://x.test/2",
                "points": 20,
                "created_at": "2024-01-02T00:00:00.000Z",
            }]},
        )

    items = asyncio.run(_make_adapter(handler).fetch(["a", "b"], SourceLimits()))
    assert len(items) == 1


def test_hn_year_from_sets_numeric_filter() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"hits": []})

    asyncio.run(
        _make_adapter(handler).fetch(["x"], SourceLimits(year_from=2023)),
    )
    assert "numericFilters" in captured["url"]
    assert "created_at_i" in captured["url"]


def test_hn_per_topic_failure_isolated() -> None:
    """A 503 on topic 1 must not stop topic 2 from contributing items."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "topic1" in str(request.url):
            return httpx.Response(503, text="bad")
        return httpx.Response(
            200,
            json={"hits": [{
                "objectID": "1",
                "title": "good",
                "url": "https://x.test",
                "points": 1,
                "created_at": "2024-01-01T00:00:00.000Z",
            }]},
        )

    items = asyncio.run(
        _make_adapter(handler).fetch(["topic1", "topic2"], SourceLimits()),
    )
    assert [it.title for it in items] == ["good"]


def test_hn_max_per_source_caps_results() -> None:
    hits = [
        {
            "objectID": str(i),
            "title": f"item {i}",
            "url": f"https://x.test/{i}",
            "points": i,
            "created_at": "2024-01-01T00:00:00.000Z",
        }
        for i in range(20)
    ]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": hits})

    items = asyncio.run(_make_adapter(handler).fetch(["x"], SourceLimits(max_per_source=5)))
    assert len(items) == 5
