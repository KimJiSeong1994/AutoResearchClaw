"""Hugging Face Daily Papers source adapter.

Replacement for the dead Papers With Code API (Meta sunset 2025-07).
HF curates a daily "trending papers" list — useful as an academic-ML
hot-list signal that does not require querying every individual source.

API shape (verified empirically; field names confirmed against
https://github.com/0x0is1/hf-papers-api-docs):

    GET https://huggingface.co/api/daily_papers?date={YYYY-MM-DD}
    -> 200 [{ "paper": {"id", "title", "summary", "authors": [{"name"}],
                        "publishedAt"},
              "upvotes": int, "numComments": int }]

The HF endpoint does NOT expose a topic search — it returns the day's
curated list. We walk back ``days_back`` days and post-filter client-side
for items whose title/abstract contains any seed-topic substring.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from paper_recommender.sources import CandidateItem, SourceLimits

log = logging.getLogger(__name__)

_HF_DAILY = "https://huggingface.co/api/daily_papers"


class HuggingFacePapersAdapter:
    name = "huggingface_papers"

    def __init__(
        self,
        *,
        days_back: int = 7,
        _transport: httpx.BaseTransport | None = None,
        _today: datetime | None = None,
    ) -> None:
        if days_back < 1:
            raise ValueError("days_back must be >= 1")
        self._days_back = days_back
        self._transport = _transport
        # Test seam — let tests pin "today" to a fixed date.
        self._today_override = _today

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        topics_lc = [t.lower() for t in seed_topics if t.strip()]

        items: list[CandidateItem] = []
        seen_arxiv_ids: set[str] = set()

        client_kwargs: dict = {"timeout": limits.timeout_sec}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        today = (self._today_override or datetime.now(timezone.utc)).date()

        async with httpx.AsyncClient(**client_kwargs) as client:
            for offset in range(self._days_back):
                if len(items) >= limits.max_per_source:
                    break
                d = today - timedelta(days=offset)
                url = f"{_HF_DAILY}?date={d.isoformat()}"
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    log.warning("hf daily fetch %s failed: %s", d, e)
                    continue

                try:
                    data = resp.json()
                except ValueError:
                    log.warning("hf daily %s returned non-json", d)
                    continue
                if not isinstance(data, list):
                    continue

                for entry in data:
                    if len(items) >= limits.max_per_source:
                        break
                    item = self._to_item(entry)
                    if item is None:
                        continue
                    if topics_lc and not self._matches_topic(item, topics_lc):
                        continue
                    if limits.year_from and item.year and item.year < limits.year_from:
                        continue
                    if item.arxiv_id and item.arxiv_id in seen_arxiv_ids:
                        continue
                    if item.arxiv_id:
                        seen_arxiv_ids.add(item.arxiv_id)
                    items.append(item)
        return items

    @staticmethod
    def _matches_topic(item: CandidateItem, topics_lc: list[str]) -> bool:
        hay = (item.title or "").lower() + " " + ((item.abstract or "")[:1000].lower())
        return any(t in hay for t in topics_lc)

    def _to_item(self, entry: dict[str, Any]) -> CandidateItem | None:
        paper = entry.get("paper")
        if not isinstance(paper, dict):
            return None
        title = (paper.get("title") or "").strip()
        if not title:
            return None

        authors_raw = paper.get("authors") or []
        names: list[str] = []
        if isinstance(authors_raw, list):
            for a in authors_raw:
                if isinstance(a, dict):
                    n = (a.get("name") or "").strip()
                    if n:
                        names.append(n)
                elif isinstance(a, str):
                    s = a.strip()
                    if s:
                        names.append(s)

        published_at = paper.get("publishedAt") or ""
        year = int(published_at[:4]) if published_at[:4].isdigit() else None

        arxiv_id = paper.get("id") or paper.get("arxiv_id")
        if arxiv_id and not isinstance(arxiv_id, str):
            arxiv_id = str(arxiv_id)
        url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else paper.get("url")

        upvotes = entry.get("upvotes")
        score = float(upvotes) if isinstance(upvotes, (int, float)) else None

        return CandidateItem(
            source=self.name,
            title=title,
            url=url,
            abstract=paper.get("summary") or None,
            authors=tuple(names),
            year=year,
            arxiv_id=arxiv_id,
            score=score,
        )
