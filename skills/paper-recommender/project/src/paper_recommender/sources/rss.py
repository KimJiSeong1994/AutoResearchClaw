"""RSS/Atom source adapter for Medium and company/research blogs.

The adapter only reads configured feed URLs. It is intentionally generic so
Medium profile/publication/topic feeds and first-party engineering blogs share
one compliance-safe path. Full article bodies are not fetched; only feed
metadata/summary snippets are emitted into the pipeline.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from paper_recommender.sources import CandidateItem, SourceLimits
from paper_recommender.sources._util import (
    clean_text,
    item_matches_topics,
    normalize_title_for_dedup,
)

log = logging.getLogger(__name__)

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_RSS_CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}


@dataclass(frozen=True)
class RssFeedSettings:
    """Configured RSS/Atom feeds.

    ``feed_urls`` may include Medium feed URLs such as
    ``https://medium.com/feed/tag/agentic-ai`` or company blog RSS feeds.
    The adapter does not discover feeds, follow article links, or scrape pages.
    """

    feed_urls: list[str] = field(default_factory=list)
    max_summary_chars: int = 700


class RssFeedAdapter:
    name = "rss"

    def __init__(
        self,
        settings: RssFeedSettings,
        *,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = _transport

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        feeds = [_safe_feed_url(u) for u in self._settings.feed_urls]
        feeds = [u for u in feeds if u]
        if not feeds:
            return []

        topics = [t.lower().strip() for t in seed_topics if t.strip()]
        items: list[CandidateItem] = []
        seen: set[str] = set()

        client_kwargs: dict = {
            "timeout": limits.timeout_sec,
            "headers": {"User-Agent": "paper-recommender/0.1 RSS metadata fetcher"},
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            for feed_url in feeds:
                if len(items) >= limits.max_per_source:
                    break
                try:
                    resp = await client.get(feed_url)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    log.warning("rss feed fetch failed for %s: %s", _redact_url(feed_url), e)
                    continue
                for item in _parse_feed(resp.content, feed_url, self._settings.max_summary_chars):
                    if len(items) >= limits.max_per_source:
                        break
                    if limits.year_from and item.year and item.year < limits.year_from:
                        continue
                    if topics and not item_matches_topics(item, topics):
                        continue
                    key = item.url or normalize_title_for_dedup(item.title)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    items.append(item)
        return items


def _safe_feed_url(raw: str) -> str | None:
    url = (raw or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        log.warning("rss feed URL rejected: invalid scheme/host")
        return None
    return url


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _parse_feed(xml_bytes: bytes, feed_url: str, max_summary_chars: int) -> list[CandidateItem]:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        log.warning("rss feed was not valid XML for %s: %s", _redact_url(feed_url), e)
        return []

    if _strip_ns(root.tag) == "feed":
        return _parse_atom(root, feed_url, max_summary_chars)
    return _parse_rss(root, feed_url, max_summary_chars)


def _parse_atom(root: ET.Element, feed_url: str, max_summary_chars: int) -> list[CandidateItem]:
    out: list[CandidateItem] = []
    feed_title = _text(root.find("a:title", _ATOM_NS)) or _feed_label(feed_url)
    for entry in root.findall("a:entry", _ATOM_NS):
        title = _text(entry.find("a:title", _ATOM_NS))
        if not title:
            continue
        link = None
        for node in entry.findall("a:link", _ATOM_NS):
            if node.get("href") and node.get("rel", "alternate") == "alternate":
                link = node.get("href")
                break
        summary = _text(entry.find("a:summary", _ATOM_NS)) or _text(entry.find("a:content", _ATOM_NS))
        published = _text(entry.find("a:published", _ATOM_NS)) or _text(entry.find("a:updated", _ATOM_NS))
        authors = tuple(
            a for a in (_text(n.find("a:name", _ATOM_NS)) for n in entry.findall("a:author", _ATOM_NS)) if a
        )
        out.append(_item(feed_url, title, link, summary, authors, feed_title, published, max_summary_chars))
    return out


def _parse_rss(root: ET.Element, feed_url: str, max_summary_chars: int) -> list[CandidateItem]:
    channel_node = root.find("channel")
    channel = channel_node if channel_node is not None else root
    feed_title = _text(channel.find("title")) or _feed_label(feed_url)
    out: list[CandidateItem] = []
    for node in channel.findall("item"):
        title = _text(node.find("title"))
        if not title:
            continue
        link = _text(node.find("link"))
        summary = (
            _text(node.find("description"))
            or _text(node.find("content:encoded", _RSS_CONTENT_NS))
        )
        published = _text(node.find("pubDate")) or _text(node.find("date"))
        creator = _text(node.find("creator")) or _text(node.find("author"))
        authors = (creator,) if creator else ()
        out.append(_item(feed_url, title, link, summary, authors, feed_title, published, max_summary_chars))
    return out


def _item(
    feed_url: str,
    title: str,
    link: str | None,
    summary: str | None,
    authors: tuple[str, ...],
    feed_title: str,
    published: str | None,
    max_summary_chars: int,
) -> CandidateItem:
    year = _year_from_date(published)
    return CandidateItem(
        source=_source_from_url(feed_url),
        title=clean_text(title),
        url=link.strip() if link else None,
        abstract=clean_text(summary)[:max_summary_chars] if summary else None,
        authors=authors,
        year=year,
        venue=feed_title,
        tags=("rss", _host(feed_url)),
    )


def _source_from_url(feed_url: str) -> str:
    host = _host(feed_url)
    if host.endswith("medium.com") or ".medium.com" in host:
        return "medium_rss"
    return "rss"


def _year_from_date(value: str | None) -> int | None:
    if not value:
        return None
    raw = value.strip()
    if len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    try:
        return parsedate_to_datetime(raw).year
    except (TypeError, ValueError, IndexError):
        return None


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    return clean_text(node.text)


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _feed_label(url: str) -> str:
    host = _host(url)
    return host or "RSS feed"


__all__ = ["RssFeedAdapter", "RssFeedSettings"]
