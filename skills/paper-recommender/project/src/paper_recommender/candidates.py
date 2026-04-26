from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from typing import Any

from paper_recommender.config import Settings
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.state import StateStore

log = logging.getLogger(__name__)


def paper_key(p: dict[str, Any]) -> str:
    # Keep the historical priority first so existing seen.json entries remain
    # effective after adding newer artifact identifiers. doc_id is last because
    # it was introduced after paper_id/id/arxiv_id/doi in persisted state.
    for k in ("paper_id", "id", "arxiv_id", "doi", "doc_id"):
        v = p.get(k)
        if v:
            return str(v)
    return f"{(p.get('title') or '').lower()}::{p.get('year')}"


# Backwards-compat alias for older imports.
_paper_key = paper_key


# arXiv "YYMM.NNNN[NN][vN]" or with optional "abs/" prefix.
_ARXIV_ID_RE = re.compile(r"^(?:abs/)?(\d{4}\.\d{4,5}(?:v\d+)?)$")
# arXiv URLs (abs or pdf), capturing the id only.
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)")
_DOI_RE = re.compile(r"^10\.\d{4,9}/[^\s]+$")


def enrich_paper_ids(p: dict[str, Any]) -> dict[str, Any]:
    """Backfill stable paper identifiers from other id-bearing fields.

    Citation-tree results can omit ``arxiv_id``/``pdf_url`` even when an
    existing id or URL proves the paper is on arXiv. Enriching locally keeps
    artifacts useful without forcing downstream PDF resolver fallbacks.
    """
    if not p.get("arxiv_id"):
        for k in ("paper_id", "id", "doc_id", "url", "pdf_url"):
            v = p.get(k)
            if not isinstance(v, str):
                continue
            m = _ARXIV_ID_RE.match(v) or _ARXIV_URL_RE.search(v)
            if m:
                p["arxiv_id"] = m.group(1)
                break
    if not p.get("pdf_url") and p.get("arxiv_id"):
        p["pdf_url"] = f"https://arxiv.org/pdf/{p['arxiv_id']}.pdf"
    if not p.get("doi"):
        for k in ("paper_id", "id", "doc_id"):
            v = p.get(k)
            if isinstance(v, str) and _DOI_RE.match(v):
                p["doi"] = v
                break
    return p


def _candidate_bucket(p: dict[str, Any]) -> str:
    if p.get("_seed_keyword"):
        return f"kw:{p['_seed_keyword']}"
    if p.get("_seed_bookmark"):
        return "citation"
    return str(p.get("source") or "unknown")


def _fair_cap(papers: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Round-robin candidates by seed/source before applying total_cap.

    The raw pool is produced by keyword-search tasks followed by citation-tree
    expansion. A plain insertion-order cap can let the first broad keyword fill
    the entire budget. Round-robin keeps retrieval diversity while preserving
    within-bucket order.
    """
    if cap <= 0:
        return []
    if len(papers) <= cap:
        return papers
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for p in papers:
        key = _candidate_bucket(p)
        if key not in buckets:
            order.append(key)
        buckets[key].append(p)

    out: list[dict[str, Any]] = []
    i = 0
    while len(out) < cap:
        progressed = False
        for key in order:
            bucket = buckets[key]
            if i < len(bucket):
                out.append(bucket[i])
                progressed = True
                if len(out) >= cap:
                    break
        if not progressed:
            break
        i += 1
    return out


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

    filtered_all: list[dict[str, Any]] = []
    dropped_bookmarked = 0
    dropped_seen = 0
    for p in pool:
        enrich_paper_ids(p)
        k = paper_key(p)
        if k in bookmarked_ids:
            dropped_bookmarked += 1
            continue
        if store.is_recently_seen(k, settings.seen.cooldown_days):
            dropped_seen += 1
            continue
        filtered_all.append(p)

    filtered = _fair_cap(filtered_all, settings.candidates.total_cap)
    bucket_counts: dict[str, int] = defaultdict(int)
    for p in filtered:
        bucket_counts[_candidate_bucket(p)] += 1

    log.info(
        "candidates: pool=%d, eligible=%d, selected=%d (cap=%d, bookmarked=%d, seen=%d, buckets=%s)",
        len(pool),
        len(filtered_all),
        len(filtered),
        settings.candidates.total_cap,
        dropped_bookmarked,
        dropped_seen,
        dict(sorted(bucket_counts.items())),
    )
    return filtered
