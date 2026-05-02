"""arxiv.org Atom feed source adapter.

Combines all seed topics into a single boolean OR query so the arxiv
API's 1-req/3-sec rate limit applies once per fetch instead of once per
topic. With 10 seed topics that is the difference between ~30s and ~6s
per pipeline run.
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import httpx

from paper_recommender.sources import CandidateItem, SourceLimits

log = logging.getLogger(__name__)

_ARXIV_API = "http://export.arxiv.org/api/query"
_ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
_REQUEST_DELAY_SEC = 3.0  # arxiv API ToS: 1 req per 3 seconds


class ArxivAdapter:
    name = "arxiv"

    def __init__(
        self,
        *,
        user_email: str = "",
        delay_sec: float = _REQUEST_DELAY_SEC,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        ua = "paper-recommender/0.1"
        if user_email:
            ua += f" (mailto:{user_email})"
        self._user_agent = ua
        self._delay_sec = delay_sec
        self._transport = _transport

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        clauses = [f'all:"{t}"' for t in seed_topics if t.strip()]
        if not clauses:
            return []
        search_query = " OR ".join(clauses)
        if limits.year_from:
            yf = f"{limits.year_from}01010000"
            yt = "99991231235959"
            search_query = f"({search_query}) AND submittedDate:[{yf} TO {yt}]"

        params = {
            "search_query": search_query,
            "start": "0",
            "max_results": str(min(max(limits.max_per_source * 2, 10), 100)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = f"{_ARXIV_API}?{urlencode(params)}"

        client_kwargs: dict = {
            "timeout": limits.timeout_sec,
            "headers": {"User-Agent": self._user_agent},
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            xml_bytes = resp.content

        if self._delay_sec > 0:
            await asyncio.sleep(self._delay_sec)

        return self._parse_atom(xml_bytes, limits)

    def _parse_atom(
        self,
        xml_bytes: bytes,
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            log.warning("arxiv response was not valid Atom XML: %s", e)
            return []

        items: list[CandidateItem] = []
        for entry in root.findall("a:entry", _ATOM_NS):
            if len(items) >= limits.max_per_source:
                break

            title = (entry.findtext("a:title", default="", namespaces=_ATOM_NS) or "").strip()
            if not title:
                continue
            abstract = (entry.findtext("a:summary", default="", namespaces=_ATOM_NS) or "").strip()

            id_text = entry.findtext("a:id", default="", namespaces=_ATOM_NS) or ""
            arxiv_id = id_text.rsplit("/abs/", 1)[-1] if "/abs/" in id_text else None

            authors = tuple(
                (a.findtext("a:name", default="", namespaces=_ATOM_NS) or "").strip()
                for a in entry.findall("a:author", _ATOM_NS)
            )
            authors = tuple(a for a in authors if a)

            published = entry.findtext("a:published", default="", namespaces=_ATOM_NS) or ""
            year: int | None
            year = int(published[:4]) if published[:4].isdigit() else None
            if limits.year_from and year is not None and year < limits.year_from:
                continue

            html_url: str | None = None
            for link in entry.findall("a:link", _ATOM_NS):
                if link.get("rel") == "alternate" and (link.get("type") or "").startswith("text/html"):
                    html_url = link.get("href")
                    break
            if not html_url and arxiv_id:
                html_url = f"https://arxiv.org/abs/{arxiv_id}"

            primary_cat = entry.find("arxiv:primary_category", _ATOM_NS)
            venue = primary_cat.get("term") if primary_cat is not None else None

            tags = tuple(
                c.get("term", "")
                for c in entry.findall("a:category", _ATOM_NS)
                if c.get("term")
            )

            items.append(
                CandidateItem(
                    source=self.name,
                    title=title,
                    url=html_url,
                    abstract=abstract or None,
                    authors=authors,
                    year=year,
                    venue=venue,
                    arxiv_id=arxiv_id,
                    tags=tags,
                )
            )
        return items
