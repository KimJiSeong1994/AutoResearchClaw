from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from paper_recommender.auth import user_id_from_jwt
from paper_recommender.candidates import _dedupe, _fair_cap, enrich_paper_ids, paper_key
from paper_recommender.config import Settings, load_settings
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.profile import build_profiles
from paper_recommender.state import StateStore
from paper_recommender.trend_queries import generate_trend_queries
from paper_recommender.trend_report import synthesize_trend_report
from paper_recommender.weekly_obsidian import write_weekly_artifacts

log = logging.getLogger(__name__)


@dataclass
class WeeklyRunResult:
    artifact_dir: Path
    wrote_artifacts: bool
    skipped: bool
    candidate_count: int
    query_count: int


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _target_dir(settings: Settings, run_at: datetime) -> Path:
    return settings.artifacts_root / run_at.strftime(settings.weekly_report.output_subdir_fmt)


def _weekly_due(settings: Settings, store: StateStore, *, now: datetime, force: bool) -> bool:
    if force:
        return True
    if settings.weekly_report.cadence_days <= 0:
        return True
    last = _parse_utc(store.last_weekly_report_at())
    if last is None:
        return True
    return now - last >= timedelta(days=settings.weekly_report.cadence_days)


async def _gather_weekly_candidates(
    settings: Settings,
    store: StateStore,
    queries: list[dict[str, str]],
    *,
    force: bool,
) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    async with JiphyClient(settings.jiphyeonjeon) as jh:
        for q in queries:
            try:
                results = await jh.search(
                    q["query"],
                    max_results=settings.weekly_report.per_query,
                    year_start=settings.weekly_report.year_start,
                    year_end=settings.weekly_report.year_end,
                )
            except Exception as e:
                log.warning("weekly search failed for %r: %s", q.get("query"), e)
                continue
            for p in results:
                if not isinstance(p, dict):
                    continue
                p = enrich_paper_ids(dict(p))
                p["_trend_query"] = q["query"]
                p["_trend_axis"] = q.get("axis")
                p["_trend_rationale"] = q.get("rationale")
                pool.append(p)
    deduped = _dedupe(pool)
    eligible: list[dict[str, Any]] = []
    for p in deduped:
        pid = paper_key(p)
        if not force and store.is_recently_weekly_seen(pid, settings.weekly_report.weekly_seen_cooldown_days):
            continue
        eligible.append(p)
    return _fair_cap(eligible, settings.weekly_report.candidate_cap)


async def run_weekly_report(
    config_path: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> WeeklyRunResult:
    settings = load_settings(config_path)
    if not settings.weekly_report.enabled:
        raise RuntimeError("weekly_report is disabled in config")
    store = StateStore(settings.state_dir)
    now = datetime.now(timezone.utc)
    run_iso = now.isoformat(timespec="seconds")
    target = _target_dir(settings, now)

    if not _weekly_due(settings, store, now=now, force=force):
        log.info("weekly report skipped; cadence not due")
        return WeeklyRunResult(target, wrote_artifacts=False, skipped=True, candidate_count=0, query_count=0)

    if dry_run:
        profile = store.load_profile(settings.profile.cache_ttl_days) or {
            "source": "seed",
            "interests": ["(dry-run) cached profile unavailable; seed topics only"],
            "keywords": list(settings.profile.seed_topics),
            "methodology_focus": [],
            "bookmark_count": 0,
        }
        narrative_md = store.load_narrative(settings.profile.cache_ttl_days)
    else:
        profile, narrative_md = await build_profiles(settings, store, force=False)
    user_id: str | None = None
    soul_md: str | None = None
    try:
        user_id = user_id_from_jwt(settings.jiphyeonjeon.token)
        soul_md = store.load_soul(user_id)
    except Exception as e:
        log.warning("weekly: could not load SOUL by user token, using profile only: %s", e)
    if not soul_md:
        soul_md = narrative_md

    queries = await generate_trend_queries(settings, soul_md, profile)
    candidates = await _gather_weekly_candidates(settings, store, queries, force=force)
    report = await synthesize_trend_report(settings, soul_md, profile, queries, candidates)

    if dry_run:
        return WeeklyRunResult(target, wrote_artifacts=False, skipped=False, candidate_count=len(candidates), query_count=len(queries))

    artifact_dir = write_weekly_artifacts(
        settings,
        profile=profile,
        soul_md=soul_md,
        user_id=user_id,
        queries=queries,
        candidates=candidates,
        report=report,
        run_iso=run_iso,
    )
    if candidates:
        store.record_weekly_seen([paper_key(p) for p in candidates[: settings.weekly_report.top_papers]])
        store.gc_weekly_seen(settings.weekly_report.weekly_seen_cooldown_days)
    store.append_weekly_report(
        {
            "run_at": run_iso,
            "user_id": user_id,
            "artifact_dir": str(artifact_dir),
            "query_count": len(queries),
            "candidate_count": len(candidates),
            "cluster_count": len(report.get("clusters") or []),
            "dry_run": False,
        }
    )
    return WeeklyRunResult(artifact_dir, wrote_artifacts=True, skipped=False, candidate_count=len(candidates), query_count=len(queries))


def run_weekly_report_sync(config_path: Path, *, force: bool = False, dry_run: bool = False) -> WeeklyRunResult:
    return asyncio.run(run_weekly_report(config_path, force=force, dry_run=dry_run))
