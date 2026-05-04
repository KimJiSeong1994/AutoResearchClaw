"""Top-level daily-research pipeline.

Wires source adapters → clustering → cluster-select → deep_bridge → daily
note. Designed for invocation from cron via ``paper-recommender
daily-research``.

Wall-clock budget per design (option d, top-3 serial): up to 90+ minutes
for a 3-cluster deep run. The outer ``asyncio.wait_for`` caps the whole
pipeline at 2 hours so a stuck subprocess can't run forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_recommender.cluster_select import select_top_clusters
from paper_recommender.clustering import (
    Cluster,
    EmbeddingClient,
    cluster_candidates,
)
from paper_recommender.config import Settings, load_settings
from paper_recommender.daily_note import SkippedCluster, compose_daily_note
from paper_recommender.deep_bridge import (
    DeepReport,
    cluster_dedup_key,
    run_deep_for_clusters,
)
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.jiphyeonjeon_auth import LoginTokenProvider, TokenProvider
from paper_recommender.llm import OpenClawLLM
from paper_recommender.sources import CandidateItem, SourceAdapter, fetch_all_sources
from paper_recommender.sources._util import normalize_title_for_dedup
from paper_recommender.sources.arxiv import ArxivAdapter
from paper_recommender.sources.hackernews import HackerNewsAdapter
from paper_recommender.sources.huggingface_papers import HuggingFacePapersAdapter
from paper_recommender.sources.google_newsletters import GoogleNewsletterMboxAdapter
from paper_recommender.sources.jiphyeonjeon import JiphyeonjeonSourceAdapter
from paper_recommender.sources.manual_links import ManualLinksAdapter
from paper_recommender.sources.rss import RssFeedAdapter
from paper_recommender.state import StateStore

log = logging.getLogger(__name__)

_OUTER_TIMEOUT_SEC = 2 * 60 * 60  # 2-hour cron-level hard cap
_MAX_SEED_TOPICS = 12


# ─────────────── output dataclass ───────────────


@dataclass
class RunResult:
    paths_written: list[Path] = field(default_factory=list)
    wall_clock_sec: float = 0.0
    source_stats: dict[str, int] = field(default_factory=dict)
    candidate_count: int = 0
    cluster_count: int = 0
    deep_success_count: int = 0
    used_fallback: bool = False
    note_markdown: str = ""


# ─────────────── public entry ───────────────


async def run_daily_research(
    config_path: Path,
    *,
    dry_run: bool = False,
    _token_provider_factory=None,
    _client_factory=None,
    _adapter_factory=None,
    _embed_client_factory=None,
    _llm_factory=None,
    _now=None,
) -> RunResult:
    """Run the daily-research pipeline once.

    The leading ``_*_factory`` keyword arguments are test seams; production
    callers pass none of them.
    """

    return await asyncio.wait_for(
        _run_impl(
            config_path,
            dry_run=dry_run,
            token_provider_factory=_token_provider_factory,
            client_factory=_client_factory,
            adapter_factory=_adapter_factory,
            embed_client_factory=_embed_client_factory,
            llm_factory=_llm_factory,
            now_fn=_now,
        ),
        timeout=_OUTER_TIMEOUT_SEC,
    )


# ─────────────── orchestration ───────────────


async def _run_impl(
    config_path: Path,
    *,
    dry_run: bool,
    token_provider_factory,
    client_factory,
    adapter_factory,
    embed_client_factory,
    llm_factory,
    now_fn,
) -> RunResult:
    t0 = time.monotonic()
    settings = load_settings(config_path)
    dr = settings.daily_research
    if dr is None:
        raise RuntimeError("daily_research section missing from config")

    store = StateStore(settings.state_dir)
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))

    # Build token provider + jiphy client (with test seam)
    if token_provider_factory is None:
        provider: TokenProvider = LoginTokenProvider(
            base_url=dr.auth.base_url,
            username=dr.auth.username,
            password=dr.auth.password,
            timeout_sec=dr.auth.timeout_sec,
        )
    else:
        provider = token_provider_factory(dr.auth)

    if client_factory is None:
        jiphy_client = JiphyClient(settings.jiphyeonjeon, token_provider=provider)
    else:
        jiphy_client = client_factory(settings.jiphyeonjeon, provider)

    async with jiphy_client:
        return await _run_with_deps(
            settings=settings,
            store=store,
            jiphy_client=jiphy_client,
            t0=t0,
            dry_run=dry_run,
            adapter_factory=adapter_factory,
            embed_client_factory=embed_client_factory,
            llm_factory=llm_factory,
            now_fn=now_fn,
        )


async def _run_with_deps(
    *,
    settings: Settings,
    store: StateStore,
    jiphy_client: JiphyClient,
    t0: float,
    dry_run: bool,
    adapter_factory,
    embed_client_factory,
    llm_factory,
    now_fn,
) -> RunResult:
    dr = settings.daily_research
    assert dr is not None
    result = RunResult()

    # ── 1. seed topics ──
    seed_topics = await _build_seed_topics(jiphy_client, settings)
    log.info("daily-research seeds (%d): %s", len(seed_topics), seed_topics)
    if not seed_topics:
        log.warning("no seed topics; will write empty note")

    # ── 2. adapters ──
    if adapter_factory is None:
        adapters = _build_adapters(dr.sources.enabled, jiphy_client, settings)
    else:
        adapters = adapter_factory(dr.sources.enabled, jiphy_client)
    if not adapters:
        log.warning("no source adapters enabled; writing empty note")

    # ── 3. fetch ──
    source_results = (
        await fetch_all_sources(adapters, seed_topics, dr.sources.limits)
        if adapters
        else {}
    )
    result.source_stats = {name: len(items) for name, items in source_results.items()}
    flat = _merge_and_dedupe(source_results)
    result.candidate_count = len(flat)

    # ── 4. cluster ──
    clusters_obj = []
    used_fallback = False
    if flat:
        if embed_client_factory is None:
            embed_client = _build_embed_client(settings)
        else:
            embed_client = embed_client_factory(settings)
        cluster_result = await cluster_candidates(
            flat,
            embed_fn=embed_client.embed_batch,
            cluster_settings=dr.cluster,
        )
        clusters_obj = cluster_result.clusters
        used_fallback = cluster_result.used_fallback
    result.used_fallback = used_fallback
    result.cluster_count = len(clusters_obj)

    # ── 5. select top + filter deep-seen ──
    picked: list[Cluster] = []
    skipped: list[SkippedCluster] = []
    deep_clusters: list[Cluster] = []
    if clusters_obj and not used_fallback:
        if llm_factory is None:
            llm = OpenClawLLM(settings.openclaw)
        else:
            llm = llm_factory(settings.openclaw)
        try:
            async with llm:
                picked = await select_top_clusters(
                    clusters_obj,
                    chat_json=llm.chat_json,
                    max_clusters=dr.cluster.max_clusters,
                    soul_md=_load_default_soul(store),
                )
        except Exception as e:
            log.warning("select_top_clusters failed; using size-fallback: %s", e)
            picked = sorted(clusters_obj, key=lambda c: -len(c.items))[
                : dr.cluster.max_clusters
            ]

        cooldown = dr.deep_seen_cooldown_days
        for c in picked:
            key = cluster_dedup_key(c)
            if store.is_recently_deep_seen(key, cooldown):
                skipped.append(SkippedCluster(c, f"deep-seen within {cooldown} days"))
            else:
                deep_clusters.append(c)

    # ── 6. deep bridge ──
    deep_reports: list[DeepReport] = []
    if not dry_run and deep_clusters:
        deep_reports = await run_deep_for_clusters(deep_clusters, dr.deep)
        # Record only SUCCESSFUL clusters as deep-seen. If a run fails for
        # infra reasons (script path, gateway down, partial stage), we want
        # the cluster eligible to retry tomorrow rather than waiting out the
        # full cooldown on a failure that wasn't the cluster's fault.
        successful_keys = [
            cluster_dedup_key(c)
            for c, r in zip(deep_clusters, deep_reports)
            if r.success
        ]
        if successful_keys:
            store.record_deep_seen(successful_keys)
        store.gc_deep_seen(dr.deep_seen_cooldown_days)
    result.deep_success_count = sum(1 for r in deep_reports if r.success)

    # ── 7. compose note ──
    run_iso = now_fn().isoformat(timespec="seconds")
    wall = time.monotonic() - t0
    note_md = compose_daily_note(
        run_iso=run_iso,
        source_stats=result.source_stats,
        candidate_count=result.candidate_count,
        clusters=picked or clusters_obj,
        deep_reports=deep_reports,
        skipped=skipped,
        used_fallback=result.used_fallback,
        wall_clock_sec=wall,
    )
    result.note_markdown = note_md

    if not dry_run:
        target_dir = settings.artifacts_root / run_iso[:10]
        target_dir.mkdir(parents=True, exist_ok=True)
        note_path = target_dir / "daily-research.md"
        note_path.write_text(note_md, encoding="utf-8")
        result.paths_written.append(note_path)

        # Per-cluster paper listing — exposes the actual recommended papers
        # so the user can browse/click without reading the synthesis prose.
        # Use clusters_obj if cluster_select returned a subset (picked) — we
        # want EVERY cluster's papers visible, not just the picked ones.
        all_clusters_for_papers = clusters_obj if clusters_obj else (picked or [])
        papers_md = _render_papers_md(
            run_iso=run_iso,
            clusters=all_clusters_for_papers,
            skipped=skipped,
        )
        papers_path = target_dir / "daily-research-papers.md"
        papers_path.write_text(papers_md, encoding="utf-8")
        result.paths_written.append(papers_path)

        raw_path = target_dir / "daily-research-raw.json"
        raw_path.write_text(
            json.dumps(
                _raw_payload(result, deep_reports, skipped, all_clusters_for_papers),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result.paths_written.append(raw_path)

    result.wall_clock_sec = time.monotonic() - t0

    # Always write last_run_status.json (even on dry-run + even on partial
    # failure) so an external health-check can spot drift. This is the single
    # most useful drift signal — it captures candidate count, deep success
    # rate, fallback usage, and seed coverage in one place.
    _write_last_run_status(
        store=store,
        run_iso=run_iso,
        result=result,
        deep_attempted=len(deep_reports),
        seed_topic_count=len(seed_topics),
        dry_run=dry_run,
    )

    return result


def _write_last_run_status(
    *,
    store: StateStore,
    run_iso: str,
    result: RunResult,
    deep_attempted: int,
    seed_topic_count: int,
    dry_run: bool,
) -> None:
    status = {
        "timestamp": run_iso,
        "dry_run": dry_run,
        "candidate_count": result.candidate_count,
        "cluster_count": result.cluster_count,
        "deep_attempted": deep_attempted,
        "deep_success_count": result.deep_success_count,
        "used_fallback": result.used_fallback,
        "source_stats": result.source_stats,
        "wall_clock_sec": result.wall_clock_sec,
        "seed_topic_count": seed_topic_count,
    }
    try:
        path = store.root / "last_run_status.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status, ensure_ascii=False, indent=2))
    except OSError as e:
        log.warning("failed to write last_run_status.json: %s", e)


# ─────────────── helpers ───────────────


async def _build_seed_topics(
    jiphy_client: JiphyClient, settings: Settings
) -> list[str]:
    """Union of bookmark-derived topics + explicit seed_topics.

    Bookmarks are sorted **newest-first** by ``created_at`` (when present) so
    recently-added bookmarks dominate the cap. Without this, the oldest
    bookmarks would permanently occupy seed slots as the bookmark list grows
    over weeks of use, drowning out current interests.
    """
    explicit = list(settings.profile.seed_topics or [])
    bookmark_topics: list[str] = []
    try:
        bookmarks = await jiphy_client.list_bookmarks()
    except Exception as e:
        log.warning("list_bookmarks failed (using explicit only): %s", e)
        bookmarks = []

    # Newest-first by created_at (ISO format sorts lexicographically). Empty
    # or missing created_at sinks to the end so untimestamped legacy bookmarks
    # don't shadow recent ones.
    bookmarks_sorted = sorted(
        bookmarks,
        key=lambda b: b.get("created_at") or "",
        reverse=True,
    )
    for bm in bookmarks_sorted:
        t = (bm.get("topic") or bm.get("title") or "").strip()
        if t:
            bookmark_topics.append(t)

    combined: list[str] = []
    seen_keys: set[str] = set()
    for t in bookmark_topics + explicit:
        key = normalize_title_for_dedup(t)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        combined.append(t)
        if len(combined) >= _MAX_SEED_TOPICS:
            break
    return combined


def _build_adapters(
    enabled: list[str],
    jiphy_client: JiphyClient,
    settings: Settings | None = None,
) -> list[SourceAdapter]:
    out: list[SourceAdapter] = []
    for name in enabled:
        if name == "arxiv":
            out.append(ArxivAdapter())
        elif name == "hackernews":
            out.append(HackerNewsAdapter())
        elif name == "jiphyeonjeon":
            out.append(JiphyeonjeonSourceAdapter(jiphy_client))
        elif name == "huggingface_papers":
            out.append(HuggingFacePapersAdapter())
        elif name == "rss":
            if settings is None or settings.daily_research is None:
                log.warning("rss requires daily_research settings — skipping")
                continue
            out.append(RssFeedAdapter(settings.daily_research.sources.rss))
        elif name == "manual_links":
            if settings is None or settings.daily_research is None:
                log.warning("manual_links requires daily_research settings — skipping")
                continue
            out.append(ManualLinksAdapter(settings.daily_research.sources.manual_links))
        elif name == "google_newsletters":
            if settings is None or settings.daily_research is None:
                log.warning(
                    "google_newsletters requires daily_research settings — skipping"
                )
                continue
            out.append(
                GoogleNewsletterMboxAdapter(
                    settings.daily_research.sources.google_newsletters
                )
            )
        else:
            log.warning("unknown source %r — skipping (Phase B.5 candidate)", name)
    return out


def _merge_and_dedupe(
    source_results: dict[str, list[CandidateItem]],
) -> list[CandidateItem]:
    """Round-robin merge across sources with academic + non-academic dedup."""

    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    out: list[CandidateItem] = []

    iters = {name: iter(items) for name, items in source_results.items()}
    while iters:
        exhausted: list[str] = []
        for name, it in list(iters.items()):
            try:
                item = next(it)
            except StopIteration:
                exhausted.append(name)
                continue

            if item.arxiv_id:
                token = f"arxiv:{item.arxiv_id.lower()}"
                if token in seen_ids:
                    continue
                seen_ids.add(token)
            elif item.doi:
                token = f"doi:{item.doi.lower()}"
                if token in seen_ids:
                    continue
                seen_ids.add(token)
            else:
                t = normalize_title_for_dedup(item.title)
                if t and t in seen_titles:
                    continue
                if t:
                    seen_titles.add(t)
            out.append(item)
        for name in exhausted:
            iters.pop(name, None)
    return out


def _build_embed_client(settings: Settings) -> EmbeddingClient:
    dr = settings.daily_research
    assert dr is not None
    base = settings.openclaw.base_url.rstrip("/")
    endpoint = dr.cluster.embedding_endpoint
    # Avoid /v1/v1/embeddings if base already ends in /v1
    if endpoint.startswith("/v1/") and base.endswith("/v1"):
        endpoint = endpoint[3:]
    url = base + endpoint
    return EmbeddingClient(
        url=url,
        token=settings.openclaw.token,
        model=dr.cluster.embedding_model,
        timeout_sec=settings.openclaw.timeout_sec,
    )


def _load_default_soul(store: StateStore) -> str | None:
    if not store.souls_dir.exists():
        return None
    candidates = sorted(store.souls_dir.glob("*.md"))
    if not candidates:
        return None
    try:
        return candidates[0].read_text(encoding="utf-8")
    except OSError:
        return None


def _serialize_item(it: CandidateItem) -> dict[str, Any]:
    return {
        "source": it.source,
        "title": it.title,
        "url": it.url,
        "abstract": (it.abstract or "")[:400] if it.abstract else None,
        "authors": list(it.authors),
        "year": it.year,
        "venue": it.venue,
        "arxiv_id": it.arxiv_id,
        "doi": it.doi,
        "score": it.score,
    }


def _serialize_cluster(c: Cluster) -> dict[str, Any]:
    return {
        "id": c.id,
        "label": c.label,
        "summary": c.summary,
        "size": len(c.items),
        "centroid_keywords": list(c.centroid_keywords),
        "coherence": c.coherence,
        "items": [_serialize_item(it) for it in c.items],
    }


def _raw_payload(
    result: RunResult,
    deep_reports: list[DeepReport],
    skipped: list[SkippedCluster],
    clusters: list[Cluster],
) -> dict[str, Any]:
    return {
        "wall_clock_sec": result.wall_clock_sec,
        "source_stats": result.source_stats,
        "candidate_count": result.candidate_count,
        "cluster_count": result.cluster_count,
        "deep_success_count": result.deep_success_count,
        "used_fallback": result.used_fallback,
        "clusters": [_serialize_cluster(c) for c in clusters],
        "deep_reports": [
            {
                "cluster_id": r.cluster_id,
                "topic": r.topic,
                "success": r.success,
                "exit_code": r.exit_code,
                "artifact_path": str(r.artifact_path) if r.artifact_path else None,
                "main_report_path": str(r.main_report_path)
                if r.main_report_path
                else None,
                "last_completed_stage": r.last_completed_stage,
                "last_completed_name": r.last_completed_name,
                "wall_clock_sec": r.wall_clock_sec,
                "error": r.error,
            }
            for r in deep_reports
        ],
        "skipped": [
            {"cluster_id": s.cluster.id, "label": s.cluster.label, "reason": s.reason}
            for s in skipped
        ],
    }


def _render_papers_md(
    *,
    run_iso: str,
    clusters: list[Cluster],
    skipped: list[SkippedCluster],
) -> str:
    """Per-cluster human-readable paper listing.

    Sits next to daily-research.md in the same artifacts dir. The wiki_publish
    step picks it up and embeds the per-cluster paper lists into the daily
    entry and the topic pages.
    """

    today = run_iso[:10]
    lines: list[str] = [
        "---",
        f'date: "{today}"',
        "type: daily-papers",
        "tags:",
        "  - autoresearch",
        "  - papers",
        "---",
        "",
        f"# Recommended Papers — {today}",
        "",
        f"_{sum(len(c.items) for c in clusters)} candidates across {len(clusters)} clusters._",
        "",
    ]

    skipped_ids = {s.cluster.id for s in skipped}

    for c in clusters:
        label = c.label or f"Cluster {c.id}"
        marker = " (skipped: deep-seen recently)" if c.id in skipped_ids else ""
        lines.append(f"## {label} ({len(c.items)} items){marker}")
        lines.append("")
        if c.summary:
            lines.append(f"_{c.summary}_")
            lines.append("")
        if not c.items:
            lines.append("_(no items)_")
            lines.append("")
            continue
        for it in c.items:
            lines.extend(_render_paper_bullet(it))
        lines.append("")

    return "\n".join(lines)


def _render_paper_bullet(it: CandidateItem) -> list[str]:
    title = (it.title or "(untitled)").replace("|", "\\|")
    meta_bits: list[str] = [it.source]
    if it.year:
        meta_bits.append(str(it.year))
    if it.venue:
        meta_bits.append(it.venue)
    meta = " · ".join(meta_bits)
    head = f"- **{title}**  _({meta})_"

    out = [head]
    if it.authors:
        authors = ", ".join(list(it.authors)[:6])
        if len(it.authors) > 6:
            authors += f", +{len(it.authors) - 6} more"
        out.append(f"  - {authors}")
    if it.url:
        out.append(f"  - [{it.url}]({it.url})")
    if it.arxiv_id:
        out.append(f"  - arxiv: `{it.arxiv_id}`")
    if it.abstract:
        # Preserve a substantial snippet so the LLM Wiki has real content.
        # CandidateItem.abstract is already capped at MAX_ABSTRACT_CHARS=1500
        # at construction; here we expose up to ~600 chars in the bullet.
        snippet = it.abstract.strip().replace("\n", " ")[:600]
        out.append(f"  - _{snippet}_" + ("…" if len(it.abstract or "") > 600 else ""))
    return out


__all__ = ["RunResult", "run_daily_research"]
