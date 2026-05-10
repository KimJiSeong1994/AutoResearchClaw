"""Tests for article_metadata.py — 10 cases covering the 4-step waterfall."""
from __future__ import annotations

import re

import pytest

from discord_openclaw_bridge.article_metadata import ArticleMetadata, fetch_article_metadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fetch(html: str):
    """Return a fetch_html mock that always returns *html*."""
    def _fetch(url: str) -> str:
        return html
    return _fetch


_ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def test_citation_tags_take_priority_over_og() -> None:
    """citation_title / citation_abstract / citation_publication_date win over og:*."""
    html = """
    <html>
    <head>
      <meta name="citation_title" content="Citation Title">
      <meta name="citation_abstract" content="Citation abstract text.">
      <meta name="citation_publication_date" content="2024-01-15">
      <meta property="og:title" content="OG Title">
      <meta property="og:description" content="OG description.">
      <meta property="article:published_time" content="2020-06-01T00:00:00Z">
    </head>
    <body></body>
    </html>
    """
    result = fetch_article_metadata("https://example.com/paper", fetch_html=_make_fetch(html))

    assert result.title == "Citation Title"
    assert result.summary == "Citation abstract text."
    assert result.published_at == "2024-01-15"


def test_og_tags_used_when_no_citation_tags() -> None:
    """og:title / og:description / article:published_time used as fallback."""
    html = """
    <html>
    <head>
      <meta property="og:title" content="OG Title">
      <meta property="og:description" content="OG description text.">
      <meta property="article:published_time" content="2024-03-20T12:00:00Z">
    </head>
    <body></body>
    </html>
    """
    result = fetch_article_metadata("https://example.com/article", fetch_html=_make_fetch(html))

    assert result.title == "OG Title"
    assert result.summary == "OG description text."
    assert result.published_at == "2024-03-20T12:00:00Z"


def test_meta_description_fallback_when_no_og_description() -> None:
    """<meta name="description"> used for summary when og:description absent."""
    html = """
    <html>
    <head>
      <meta property="og:title" content="Page Title">
      <meta name="description" content="Plain meta description.">
    </head>
    <body></body>
    </html>
    """
    result = fetch_article_metadata("https://example.com/page", fetch_html=_make_fetch(html))

    assert result.title == "Page Title"
    assert result.summary == "Plain meta description."
    assert result.published_at == ""


def test_first_p_in_article_fallback() -> None:
    """First <p> inside <article> used for summary when no meta description."""
    html = """
    <html>
    <head>
      <title>Article Title</title>
    </head>
    <body>
      <article>
        <p>This is the article abstract paragraph.</p>
        <p>Second paragraph should not appear.</p>
      </article>
    </body>
    </html>
    """
    result = fetch_article_metadata("https://example.com/art", fetch_html=_make_fetch(html))

    assert result.title == "Article Title"
    assert result.summary == "This is the article abstract paragraph."


def test_first_p_in_main_fallback() -> None:
    """First <p> inside <main> used for summary when no meta description."""
    html = """
    <html>
    <head>
      <title>Main Title</title>
    </head>
    <body>
      <main>
        <p>Main section first paragraph text.</p>
        <p>Ignored second paragraph.</p>
      </main>
    </body>
    </html>
    """
    result = fetch_article_metadata("https://example.com/main", fetch_html=_make_fetch(html))

    assert result.summary == "Main section first paragraph text."


def test_non_html_returns_blank_metadata() -> None:
    """Empty string from fetch_html (simulating non-HTML content-type) → all blank."""
    result = fetch_article_metadata("https://example.com/file.pdf", fetch_html=_make_fetch(""))

    assert result.url == "https://example.com/file.pdf"
    assert result.title == ""
    assert result.summary == ""
    assert result.published_at == ""
    assert _ISO_UTC_RE.match(result.fetched_at), f"Invalid fetched_at: {result.fetched_at!r}"


def test_fetch_exception_returns_blank_metadata_without_raising() -> None:
    """Network error / timeout → blank metadata, no exception propagated."""
    def _raise(url: str) -> str:
        raise OSError("connection refused")

    result = fetch_article_metadata("https://example.com/err", fetch_html=_raise)

    assert result.url == "https://example.com/err"
    assert result.title == ""
    assert result.summary == ""
    assert result.published_at == ""
    # fetched_at still set before the failed fetch
    assert _ISO_UTC_RE.match(result.fetched_at), f"Invalid fetched_at: {result.fetched_at!r}"


def test_truncated_html_parses_without_error() -> None:
    """max_bytes truncation produces partial HTML that still parses gracefully."""
    full_html = (
        "<html><head>"
        '<meta name="citation_title" content="Truncated Paper">'
        '<meta name="citation_abstract" content="Abstract here.">'
        "</head><body><article><p>Body text.</p></article></body></html>"
    )
    # Truncate before the closing tags to simulate max_bytes cutoff
    truncated = full_html[:120]

    result = fetch_article_metadata(
        "https://example.com/truncated",
        fetch_html=_make_fetch(truncated),
    )

    # At minimum the URL must be preserved; title may or may not be parsed
    assert result.url == "https://example.com/truncated"
    assert _ISO_UTC_RE.match(result.fetched_at)
    # No exception raised — that's the key contract


def test_citation_beats_og_for_summary_only() -> None:
    """citation_abstract is preferred over og:description even when og:title present."""
    html = """
    <html>
    <head>
      <meta property="og:title" content="OG Title">
      <meta property="og:description" content="OG desc">
      <meta name="citation_abstract" content="Citation abstract wins.">
    </head>
    </html>
    """
    result = fetch_article_metadata("https://example.com/mixed", fetch_html=_make_fetch(html))

    assert result.summary == "Citation abstract wins."
    # og:title used for title since no citation_title
    assert result.title == "OG Title"


def test_fetched_at_is_iso_utc() -> None:
    """fetched_at is always a valid ISO 8601 UTC timestamp."""
    html = "<html><head><title>Test</title></head></html>"
    result = fetch_article_metadata("https://example.com/ts", fetch_html=_make_fetch(html))

    assert _ISO_UTC_RE.match(result.fetched_at), f"fetched_at not ISO UTC: {result.fetched_at!r}"


def test_url_preserved_in_result() -> None:
    """url field in ArticleMetadata matches the input url exactly."""
    url = "https://nature.com/articles/s41586-024-99999-0"
    html = '<html><head><meta property="og:title" content="Nature Paper"></head></html>'
    result = fetch_article_metadata(url, fetch_html=_make_fetch(html))

    assert result.url == url
    assert isinstance(result, ArticleMetadata)
