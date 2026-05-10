"""article_metadata.py — Best-effort article metadata extraction for seed expansion.

4-step resolution waterfall (per design §3.2):
  1. <meta name="citation_title|citation_abstract|citation_publication_date">
  2. og:title / og:description / article:published_time
  3. <meta name="description">
  4. first <p> in <article> or <main>

Never raises; returns blank fields on any error.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable

from .miner import (
    _COLLECTION_FETCH_TIMEOUT_SEC,
    _COLLECTION_USER_AGENT,
    _safe_url_open,
    clean_text,
)


@dataclass(frozen=True)
class ArticleMetadata:
    url: str
    title: str = ""
    summary: str = ""       # abstract / og:description / first <p>
    published_at: str = ""  # ISO date
    fetched_at: str = ""    # ISO datetime UTC


def fetch_article_metadata(
    url: str,
    *,
    timeout_sec: float = _COLLECTION_FETCH_TIMEOUT_SEC,
    user_agent: str = _COLLECTION_USER_AGENT,
    max_bytes: int = 800_000,
    fetch_html: Callable[[str], str] | None = None,
) -> ArticleMetadata:
    """Best-effort metadata extraction. Returns blank fields on failure
    (never raises). Resolution waterfall:
      1. <meta name="citation_title|citation_abstract|citation_publication_date">
      2. og:title / og:description / article:published_time
      3. <meta name="description">
      4. first <p> in <article> or <main>
    """
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        if fetch_html is not None:
            html_text = fetch_html(url)
        else:
            html_text = _fetch_html_default(
                url, timeout_sec=timeout_sec, user_agent=user_agent, max_bytes=max_bytes
            )
    except Exception:
        return ArticleMetadata(url=url, fetched_at=fetched_at)

    if not html_text:
        return ArticleMetadata(url=url, fetched_at=fetched_at)

    meta = _extract_metadata(html_text)
    return ArticleMetadata(
        url=url,
        title=meta["title"],
        summary=meta["summary"],
        published_at=meta["published_at"],
        fetched_at=fetched_at,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_html_default(
    url: str,
    *,
    timeout_sec: float,
    user_agent: str,
    max_bytes: int,
) -> str:
    """Fetch up to *max_bytes* of HTML, blocking SSRF via redirect re-validation."""
    with _safe_url_open(url, timeout=timeout_sec, user_agent=user_agent) as response:
        content_type = response.headers.get("Content-Type", "")
        if content_type and "html" not in content_type.lower():
            return ""
        return response.read(max_bytes).decode("utf-8", "replace")


def _extract_metadata(html_text: str) -> dict[str, str]:
    parser = _ArticleMetadataParser()
    parser.feed(html_text)
    return parser.result()


class _ArticleMetadataParser(HTMLParser):
    """Single-pass HTMLParser extracting title, summary, and published_at."""

    def __init__(self) -> None:
        super().__init__()
        self._meta: dict[str, str] = {}
        self._capture_title = False
        self._title_parts: list[str] = []
        self._in_article_or_main = False
        self._in_first_p = False
        self._first_p_parts: list[str] = []
        self._first_p_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_lower = tag.lower()

        if tag_lower == "title":
            self._capture_title = True
            return

        if tag_lower in {"article", "main"}:
            self._in_article_or_main = True
            return

        if tag_lower == "p":
            if self._in_article_or_main and not self._first_p_done:
                self._in_first_p = True
            return

        if tag_lower != "meta":
            return

        values = {name.lower(): (value or "") for name, value in attrs}
        key = (values.get("property") or values.get("name") or "").lower()
        content = clean_text(values.get("content"), limit=1000)
        if key and content and key not in self._meta:
            self._meta[key] = content

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)
        if self._in_first_p and not self._first_p_done:
            self._first_p_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if tag_lower == "title":
            self._capture_title = False
        elif tag_lower == "p":
            if self._in_first_p:
                self._in_first_p = False
                self._first_p_done = True
        elif tag_lower in {"article", "main"}:
            self._in_article_or_main = False

    def result(self) -> dict[str, str]:
        # Title: citation > og > twitter > <title>
        title = (
            self._meta.get("citation_title")
            or self._meta.get("og:title")
            or self._meta.get("twitter:title")
            or clean_text(" ".join(self._title_parts), limit=500)
            or ""
        )

        # Summary: citation_abstract > og > twitter > meta description > first <p>
        first_p_text = clean_text("".join(self._first_p_parts), limit=700)
        summary = (
            self._meta.get("citation_abstract")
            or self._meta.get("og:description")
            or self._meta.get("twitter:description")
            or self._meta.get("description")
            or first_p_text
            or ""
        )

        # Published at: citation > article:published_time > date
        published_at = (
            self._meta.get("citation_publication_date")
            or self._meta.get("article:published_time")
            or self._meta.get("date")
            or ""
        )

        return {
            "title": clean_text(title, limit=300) if title else "",
            "summary": clean_text(summary, limit=700) if summary else "",
            "published_at": clean_text(published_at, limit=40) if published_at else "",
        }
