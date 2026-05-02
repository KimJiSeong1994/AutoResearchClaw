"""SourceAdapter wrapping the existing JiphyClient.

Does NOT own the HTTP client — accepts an injected ``JiphyClient`` so the
orchestrator can share one authenticated session across multiple adapter
calls and JiphyClient methods (``list_bookmarks``, ``citation_tree``, etc.).
"""

from __future__ import annotations

import logging
from typing import Any

from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.sources import CandidateItem, SourceLimits

log = logging.getLogger(__name__)


class JiphyeonjeonSourceAdapter:
    name = "jiphyeonjeon"

    def __init__(self, client: JiphyClient) -> None:
        self._client = client

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        topics = [t for t in seed_topics if t.strip()]
        if not topics:
            return []

        items: list[CandidateItem] = []
        seen_keys: set[str] = set()
        per_topic = max(1, limits.max_per_source // max(1, len(topics)))

        for topic in topics:
            if len(items) >= limits.max_per_source:
                break
            try:
                papers = await self._client.search(
                    topic,
                    max_results=per_topic,
                    year_start=limits.year_from,
                )
            except Exception as e:
                log.warning("jiphyeonjeon search %r failed: %s", topic, e)
                continue

            for p in papers:
                if len(items) >= limits.max_per_source:
                    break
                item = self._to_item(p)
                if item is None:
                    continue
                key = item.arxiv_id or item.doi or item.title.lower().strip()
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                items.append(item)
        return items

    def _to_item(self, p: dict[str, Any]) -> CandidateItem | None:
        title = (p.get("title") or "").strip()
        if not title:
            return None

        authors_raw = p.get("authors") or []
        if isinstance(authors_raw, list):
            authors = tuple(str(a).strip() for a in authors_raw if a)
        else:
            authors = ()

        year = p.get("year") if isinstance(p.get("year"), int) else None

        score: float | None = None
        raw_score = p.get("score")
        if isinstance(raw_score, (int, float)):
            score = float(raw_score)

        return CandidateItem(
            source=self.name,
            title=title,
            url=p.get("url") or None,
            abstract=p.get("abstract") or None,
            authors=authors,
            year=year,
            venue=p.get("venue") or p.get("source") or None,
            arxiv_id=p.get("arxiv_id") or None,
            doi=p.get("doi") or None,
            score=score,
        )
