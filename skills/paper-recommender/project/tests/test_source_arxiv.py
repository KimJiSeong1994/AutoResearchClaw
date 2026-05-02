from __future__ import annotations

import asyncio

import httpx

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.arxiv import ArxivAdapter

_SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Sample Paper One</title>
    <summary>Abstract one.</summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Alice</name></author>
    <author><name>Bob</name></author>
    <link rel="alternate" type="text/html" href="https://arxiv.org/abs/2401.00001"/>
    <link rel="related" type="application/pdf" href="https://arxiv.org/pdf/2401.00001"/>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <category term="cs.AI"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2301.99999v2</id>
    <title>Old Paper</title>
    <summary>Older.</summary>
    <published>2023-06-01T00:00:00Z</published>
    <author><name>Carol</name></author>
    <link rel="alternate" type="text/html" href="https://arxiv.org/abs/2301.99999"/>
  </entry>
</feed>
"""


def _make_adapter(handler) -> ArxivAdapter:
    return ArxivAdapter(delay_sec=0.0, _transport=httpx.MockTransport(handler))


def test_arxiv_happy_path() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, content=_SAMPLE_FEED)

    items = asyncio.run(
        _make_adapter(handler).fetch(["transformer"], SourceLimits(max_per_source=10)),
    )

    assert "/api/query" in captured["url"]
    assert "search_query=" in captured["url"]
    assert (captured["ua"] or "").startswith("paper-recommender/")

    assert len(items) == 2
    p1 = items[0]
    assert p1.title == "Sample Paper One"
    assert p1.arxiv_id == "2401.00001v1"
    assert p1.url == "https://arxiv.org/abs/2401.00001"
    assert p1.year == 2024
    assert p1.authors == ("Alice", "Bob")
    assert p1.venue == "cs.LG"
    assert "cs.LG" in p1.tags and "cs.AI" in p1.tags


def test_arxiv_combines_topics_with_or() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["calls"] = captured.get("calls", 0) + 1
        return httpx.Response(200, content=_SAMPLE_FEED)

    asyncio.run(
        _make_adapter(handler).fetch(["topic1", "topic2", "topic3"], SourceLimits()),
    )
    qs = captured["url"]
    assert "topic1" in qs and "topic2" in qs and "topic3" in qs
    assert captured["calls"] == 1, "must be a single API call across all topics"


def test_arxiv_year_from_filters_old_entries() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SAMPLE_FEED)

    items = asyncio.run(_make_adapter(handler).fetch(["x"], SourceLimits(year_from=2024)))
    titles = {i.title for i in items}
    assert "Sample Paper One" in titles
    assert "Old Paper" not in titles


def test_arxiv_max_per_source_caps_results() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SAMPLE_FEED)

    items = asyncio.run(_make_adapter(handler).fetch(["x"], SourceLimits(max_per_source=1)))
    assert len(items) == 1


def test_arxiv_handles_malformed_xml() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<not-valid-xml")

    items = asyncio.run(_make_adapter(handler).fetch(["x"], SourceLimits()))
    assert items == []


def test_arxiv_skips_entries_without_title() -> None:
    no_title = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001</id>
    <title></title>
  </entry>
</feed>"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=no_title)

    items = asyncio.run(_make_adapter(handler).fetch(["x"], SourceLimits()))
    assert items == []


def test_arxiv_picks_html_link_not_pdf() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SAMPLE_FEED)

    items = asyncio.run(_make_adapter(handler).fetch(["x"], SourceLimits()))
    assert items[0].url == "https://arxiv.org/abs/2401.00001"
    assert "pdf" not in (items[0].url or "")


def test_arxiv_empty_topics_returns_empty() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_SAMPLE_FEED)

    items = asyncio.run(_make_adapter(handler).fetch([], SourceLimits()))
    assert items == []
