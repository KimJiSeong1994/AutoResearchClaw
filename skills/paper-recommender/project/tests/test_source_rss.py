from __future__ import annotations

import asyncio

import httpx

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.rss import RssFeedAdapter, RssFeedSettings


def _adapter(handler, feeds: list[str]) -> RssFeedAdapter:
    return RssFeedAdapter(
        RssFeedSettings(feed_urls=feeds, max_summary_chars=80),
        _transport=httpx.MockTransport(handler),
    )


def test_rss_adapter_reads_medium_rss_metadata_only() -> None:
    xml = b"""
    <rss version="2.0"><channel>
      <title>Medium Agentic AI</title>
      <item>
        <title>Agentic AI governance patterns</title>
        <link>https://medium.com/@writer/agentic-ai-governance</link>
        <description>How teams govern agentic AI workflows without scraping.</description>
        <pubDate>Mon, 04 May 2026 09:00:00 GMT</pubDate>
        <author>writer@example.test</author>
      </item>
    </channel></rss>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://medium.com/feed/tag/agentic-ai"
        return httpx.Response(200, content=xml)

    items = asyncio.run(
        _adapter(handler, ["https://medium.com/feed/tag/agentic-ai"]).fetch(
            ["agentic ai"], SourceLimits(max_per_source=10, year_from=2024)
        )
    )

    assert len(items) == 1
    item = items[0]
    assert item.source == "medium_rss"
    assert item.title == "Agentic AI governance patterns"
    assert item.url == "https://medium.com/@writer/agentic-ai-governance"
    assert item.year == 2026
    assert item.venue == "Medium Agentic AI"
    assert item.tags == ("rss", "medium.com")
    assert item.abstract and "govern agentic AI" in item.abstract


def test_rss_adapter_reads_atom_company_blog_and_filters_topics() -> None:
    xml = b"""
    <feed xmlns="http://www.w3.org/2005/Atom">
      <title>Company Research Blog</title>
      <entry>
        <title>Agentic data cloud for research workflows</title>
        <link href="https://blog.example.test/agentic-data" />
        <summary>Evidence pipelines for AI agents.</summary>
        <updated>2026-05-03T00:00:00Z</updated>
        <author><name>Alice</name></author>
      </entry>
      <entry>
        <title>Unrelated cooking post</title>
        <link href="https://blog.example.test/cooking" />
        <summary>Food.</summary>
        <updated>2026-05-03T00:00:00Z</updated>
      </entry>
    </feed>
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml)

    items = asyncio.run(
        _adapter(handler, ["https://blog.example.test/feed.xml"]).fetch(
            ["research workflows"], SourceLimits(max_per_source=10)
        )
    )

    assert [it.title for it in items] == ["Agentic data cloud for research workflows"]
    assert items[0].source == "rss"
    assert items[0].authors == ("Alice",)


def test_rss_adapter_rejects_invalid_feed_url() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("invalid URL should not be fetched")

    items = asyncio.run(_adapter(handler, ["file:///private.xml"]).fetch(["x"], SourceLimits()))
    assert items == []
