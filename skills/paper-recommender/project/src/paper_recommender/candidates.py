from __future__ import annotations

import asyncio
import logging
from typing import Any

from paper_recommender.config import Settings
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.state import StateStore

log = logging.getLogger(__name__)


def paper_key(p: dict[str, Any]) -> str:
    for k in ("paper_id", "id", "arxiv_id", "doi"):
        v = p.get(k)
        if v:
            return str(v)
    return f"{(p.get('title') or '').lower()}::{p.get('year')}"


# Backwards-compat alias for older imports.
_paper_key = paper_key


def _dedupe(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for p in papers:
        k = paper_key(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


async def gather_candidates(
    settings: Settings,
    store: StateStore,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    keywords = list(profile.get("keywords") or settings.profile.seed_topics)
    if not keywords:
        log.warning("no keywords in profile; nothing to gather")
        return []

    async with JiphyClient(settings.jiphyeonjeon) as jh:
        bookmarks = await jh.list_bookmarks()
        bookmarked_ids = {paper_key(b) for b in bookmarks if isinstance(b, dict)}

        search_tasks = [
            jh.search(
                kw,
                max_results=settings.candidates.per_keyword,
                year_start=settings.candidates.year_start,
                year_end=settings.candidates.year_end,
            )
            for kw in keywords
        ]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        pool: list[dict[str, Any]] = []
        for kw, res in zip(keywords, search_results):
            if isinstance(res, Exception):
                log.warning("search failed for %r: %s", kw, res)
                continue
            for p in res:
                p["_seed_keyword"] = kw
                pool.append(p)

        top_bms = [b for b in bookmarks[: settings.candidates.related_from_top_n_bookmarks] if b.get("id")]
        related_tasks = [
            jh.citation_tree(
                bookmark_id=str(b["id"]),
                depth=2,
                max_per_direction=settings.candidates.related_per_bookmark,
            )
            for b in top_bms
        ]
        related_results = await asyncio.gather(*related_tasks, return_exceptions=True)
        for bm, res in zip(top_bms, related_results):
            if isinstance(res, Exception):
                log.warning("citation-tree failed for bookmark %s: %s", bm.get("id"), res)
                continue
            for direction in ("forward", "backward", "neighbors", "papers"):
                items = res.get(direction) if isinstance(res, dict) else None
                if isinstance(items, list):
                    for p in items:
                        if isinstance(p, dict):
                            p["_seed_bookmark"] = bm.get("id")
                            pool.append(p)

    pool = _dedupe(pool)

    filtered: list[dict[str, Any]] = []
    for p in pool:
        k = paper_key(p)
        if k in bookmarked_ids:
            continue
        if store.is_recently_seen(k, settings.seen.cooldown_days):
            continue
        filtered.append(p)
        if len(filtered) >= settings.candidates.total_cap:
            break

    log.info(
        "candidates: pool=%d, after bookmark/seen filter=%d (cap=%d)",
        len(pool),
        len(filtered),
        settings.candidates.total_cap,
    )
    return filtered
