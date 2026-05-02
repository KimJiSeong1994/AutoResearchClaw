"""Hacker News Algolia search source adapter.

Algolia exposes a free, unauthenticated JSON search over HN posts.
We query per seed topic and merge with object-id-level dedup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from paper_recommender.sources import CandidateItem, SourceLimits
from paper_recommender.sources._util import normalize_title_for_dedup

log = logging.getLogger(__name__)

_HN_SEARCH = "https://hn.algolia.com/api/v1/search"


class HackerNewsAdapter:
    name = "hackernews"

    def __init__(
        self,
        *,
        tags: str = "story",
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        # tags can be e.g. "story" (any story) or "(story,show_hn)" (Show HN only).
        self._tags = tags
        self._transport = _transport

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        topics = [t for t in seed_topics if t.strip()]
        if not topics:
            return []

        ts_filter = ""
        if limits.year_from:
            ts = int(datetime(limits.year_from, 1, 1, tzinfo=timezone.utc).timestamp())
            ts_filter = f"created_at_i>{ts}"

        items: list[CandidateItem] = []
        seen_ids: set[str] = set()
        seen_norm_titles: set[str] = set()

        client_kwargs: dict = {"timeout": limits.timeout_sec}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            for topic in topics:
                if len(items) >= limits.max_per_source:
                    break
                params = {
                    "query": topic,
                    "tags": self._tags,
                    "hitsPerPage": str(min(max(limits.max_per_source, 10), 50)),
                }
                if ts_filter:
                    params["numericFilters"] = ts_filter

                url = f"{_HN_SEARCH}?{urlencode(params)}"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    log.warning("hn fetch failed for %r: %s", topic, e)
                    continue

                data = resp.json()
                for hit in data.get("hits", []):
                    if len(items) >= limits.max_per_source:
                        break
                    obj_id = str(hit.get("objectID") or "")
                    if obj_id and obj_id in seen_ids:
                        continue
                    item = self._to_item(hit)
                    if item is None:
                        continue
                    norm = normalize_title_for_dedup(item.title)
                    if norm and norm in seen_norm_titles:
                        continue
                    if obj_id:
                        seen_ids.add(obj_id)
                    if norm:
                        seen_norm_titles.add(norm)
                    items.append(item)
        return items

    def _to_item(self, hit: dict) -> CandidateItem | None:
        title = (hit.get("title") or "").strip()
        if not title:
            return None
        url = hit.get("url")
        obj_id = hit.get("objectID")
        if not url and obj_id:
            url = f"https://news.ycombinator.com/item?id={obj_id}"

        created_at = hit.get("created_at") or ""
        year = int(created_at[:4]) if created_at[:4].isdigit() else None

        author = hit.get("author")
        authors: tuple[str, ...] = (author,) if isinstance(author, str) and author else ()

        points = hit.get("points")
        score = float(points) if isinstance(points, (int, float)) else None

        return CandidateItem(
            source=self.name,
            title=title,
            url=url,
            abstract=None,
            authors=authors,
            year=year,
            venue="Hacker News",
            score=score,
        )
