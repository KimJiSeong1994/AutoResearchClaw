from __future__ import annotations

import asyncio
import hashlib
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


@dataclass(frozen=True)
class SoulRunContext:
    """Auditable SOUL state used by the weekly trend pipeline.

    `source` is deliberately explicit so downstream renderers can distinguish a
    real SOUL-backed run from a profile/narrative fallback. The compact card is
    the prompt/search surface; the full text remains only as a snapshot artifact.
    """

    active_md: str | None
    compact_card: str | None
    provenance: dict[str, Any]


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


def _sha256_text(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compact_soul_card(soul_md: str | None, profile: dict[str, Any], *, limit: int = 1600) -> str | None:
    """Create the small SOUL surface that is safe to feed into query/report LLMs.

    Full SOUL files tend to accrete audit logs, blind spots, and historical
    notes. Those are useful for governance, but noisy as search-query context.
    This card keeps stable identity + active profile signals and drops obvious
    maintenance sections.
    """

    lines: list[str] = []
    for key, label in (
        ("interests", "Interests"),
        ("keywords", "Keywords"),
        ("methodology_focus", "Methodology"),
    ):
        vals = profile.get(key) or []
        if isinstance(vals, list) and vals:
            joined = ", ".join(str(v).strip() for v in vals[:8] if str(v).strip())
            if joined:
                lines.append(f"{label}: {joined}")

    skip_markers = (
        "changelog",
        "change log",
        "history",
        "audit",
        "log",
        "blind spot",
        "blindspot",
        "negative",
        "suppress",
        "suppression",
        "caveat",
        "운영",
        "변경",
        "로그",
        "제외",
        "억제",
    )
    seen = {line.lower() for line in lines}
    if soul_md:
        for raw in soul_md.splitlines():
            line = raw.strip().strip("#-*`> \t")
            if not line or len(line) < 8:
                continue
            lowered = line.lower()
            if any(marker in lowered for marker in skip_markers):
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            lines.append(line)
            if sum(len(item) + 1 for item in lines) >= limit:
                break

    card = "\n".join(lines).strip()
    return card[:limit].strip() or None


def _build_soul_context(
    settings: Settings,
    store: StateStore,
    *,
    user_id: str | None,
    profile: dict[str, Any],
    narrative_md: str | None,
) -> SoulRunContext:
    source = "absent"
    soul_md: str | None = None
    soul_last_updated: str | None = None
    if user_id:
        soul_md = store.load_soul(user_id)
        soul_last_updated = store.last_soul_update(user_id)
        if soul_md:
            source = "soul"
    active_md = soul_md
    fallback_used = False
    if not active_md and narrative_md:
        active_md = narrative_md
        source = "profile_narrative_fallback"
        fallback_used = True
    elif not active_md:
        fallback_used = True

    compact_card = _compact_soul_card(
        active_md,
        profile,
        limit=min(max(settings.soul.compact_at_bytes, 800), settings.soul.max_bytes or 1600),
    )
    active_bytes = len(active_md.encode("utf-8")) if active_md else 0
    provenance: dict[str, Any] = {
        "source": source,
        "user_id": user_id,
        "present": source == "soul",
        "fallback_used": fallback_used,
        "active_bytes": active_bytes,
        "active_sha256": _sha256_text(active_md),
        "compact_card_bytes": len(compact_card.encode("utf-8")) if compact_card else 0,
        "compact_card_sha256": _sha256_text(compact_card),
        "soul_last_updated": soul_last_updated,
        "max_bytes": settings.soul.max_bytes,
        "compact_at_bytes": settings.soul.compact_at_bytes,
    }
    return SoulRunContext(active_md=active_md, compact_card=compact_card, provenance=provenance)


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
    try:
        user_id = user_id_from_jwt(settings.jiphyeonjeon.token)
    except Exception as e:
        log.warning("weekly: could not load SOUL by user token, using profile only: %s", e)
    soul_context = _build_soul_context(
        settings,
        store,
        user_id=user_id,
        profile=profile,
        narrative_md=narrative_md,
    )
    if soul_context.provenance.get("fallback_used"):
        log.warning("weekly: SOUL fallback active: %s", soul_context.provenance.get("source"))

    queries = await generate_trend_queries(settings, soul_context.compact_card, profile)
    candidates = await _gather_weekly_candidates(settings, store, queries, force=force)
    report = await synthesize_trend_report(settings, soul_context.compact_card, profile, queries, candidates)

    if dry_run:
        return WeeklyRunResult(target, wrote_artifacts=False, skipped=False, candidate_count=len(candidates), query_count=len(queries))

    artifact_dir = write_weekly_artifacts(
        settings,
        profile=profile,
        soul_md=soul_context.active_md,
        soul_card=soul_context.compact_card,
        soul_provenance=soul_context.provenance,
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
            "soul_provenance": soul_context.provenance,
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
