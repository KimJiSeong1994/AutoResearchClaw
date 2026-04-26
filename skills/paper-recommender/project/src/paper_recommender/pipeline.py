from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from paper_recommender.auth import user_id_from_jwt
from paper_recommender.candidates import gather_candidates, paper_key
from paper_recommender.config import Settings, load_settings
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.obsidian import write_artifacts
from paper_recommender.profile import build_profiles
from paper_recommender.rerank import rerank_candidates, score_stats
from paper_recommender.signals import apply_decay, collect_feedback
from paper_recommender.soul import (
    diff_new_bookmarks,
    extract_suppress_keywords,
    update_soul,
)
from paper_recommender.state import StateStore

log = logging.getLogger(__name__)


def _parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _soul_cadence_due(settings: Settings, store: StateStore, user_id: str) -> bool:
    cadence_days = settings.soul.update_cadence_days
    if cadence_days <= 0:
        return True
    last = _parse_utc_datetime(store.last_soul_update(user_id))
    if last is None:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(days=cadence_days)


_ALLOWED_MODES = {"keywords", "narrative", "soul", "ab"}


def _resolve_variants(
    settings: Settings,
    *,
    narrative_available: bool,
    soul_available: bool,
) -> list[str]:
    mode = settings.rerank.mode
    if mode not in _ALLOWED_MODES:
        log.warning("unknown rerank.mode=%r; falling back to 'keywords'", mode)
        mode = "keywords"
    if mode == "soul":
        return ["soul"] if soul_available else ["keywords"]
    if mode == "narrative":
        return ["narrative"] if narrative_available else ["keywords"]
    if mode == "ab":
        # Prefer soul > narrative as the 'rich' variant compared against keywords.
        if soul_available:
            return ["keywords", "soul"]
        if narrative_available:
            return ["keywords", "narrative"]
        return ["keywords"]
    return [mode]


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


_SUPPRESS_MIN_TERM_LEN = 3
_SUPPRESS_MAX_TERMS = 50


def _expand_suppress_terms(terms: list[str]) -> list[str]:
    """Split comma-joined reasons, drop very short fragments, dedupe (case-insensitive).

    A dislike reason like "too systems-y, not my area" expands to two real
    suppress phrases. A short fragment like "a" is dropped to prevent the
    pattern from matching every candidate.
    """
    expanded: list[str] = []
    for term in terms:
        if not term:
            continue
        for piece in re.split(r"[,;\n]+", term):
            piece = piece.strip()
            if len(piece) < _SUPPRESS_MIN_TERM_LEN:
                continue
            expanded.append(piece)
    seen: set[str] = set()
    out: list[str] = []
    for t in expanded:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:_SUPPRESS_MAX_TERMS]


def _apply_suppress(
    candidates: list[dict[str, Any]],
    suppress_terms: list[str],
) -> tuple[list[dict[str, Any]], int]:
    expanded = _expand_suppress_terms(suppress_terms)
    if not expanded:
        return candidates, 0
    patterns = [re.compile(re.escape(term), re.IGNORECASE) for term in expanded]
    kept: list[dict[str, Any]] = []
    dropped = 0
    for c in candidates:
        blob = " ".join(
            str(c.get(k) or "")
            for k in ("title", "abstract", "summary", "_seed_keyword")
        )
        if any(p.search(blob) for p in patterns):
            dropped += 1
            continue
        kept.append(c)
    return kept, dropped


def _recent_picks_from_ab_log(store: StateStore, days: int) -> list[dict[str, Any]]:
    if days <= 0 or not store.ab_log_path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    picks: list[dict[str, Any]] = []
    try:
        import json
        for line in store.ab_log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_at = entry.get("run_at", "")
            try:
                ts = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            for variant_picks in (entry.get("variants") or {}).values():
                if isinstance(variant_picks, list):
                    for p in variant_picks:
                        if isinstance(p, dict):
                            picks.append(p)
    except OSError as e:
        log.warning("could not read ab_log for recent picks: %s", e)
    # Dedupe by paper_id, keep highest score
    by_id: dict[str, dict[str, Any]] = {}
    for p in picks:
        pid = str(p.get("paper_id") or "")
        if not pid:
            continue
        prev = by_id.get(pid)
        if prev is None or (p.get("score") or 0) > (prev.get("score") or 0):
            by_id[pid] = p
    return list(by_id.values())


def _collect_feedback(settings: Settings, store: StateStore) -> tuple[list[dict[str, Any]], int]:
    """Scan the feedback inbox (populated by sync-results.sh).

    Persistence-first: we only return records the SOUL pipeline can rely on
    after the log append succeeds. If persistence fails, drop the in-memory
    records so the next run will re-collect them from inbox files (at-least-
    once delivery into SOUL — never an unlogged signal that influenced SOUL).
    """
    if not settings.feedback.enabled:
        return [], 0
    inbox = store.feedback_inbox_dir(settings.feedback.inbox_subdir)
    processed = store.load_processed_feedback_keys()
    today = datetime.now(timezone.utc).date()
    records = collect_feedback(
        inbox,
        today=today,
        lookback_days=settings.feedback.lookback_days,
        max_file_bytes=settings.feedback.max_file_kb * 1024,
        already_processed=processed,
    )
    dicts = [r.to_dict() for r in records]
    if not dicts:
        return [], len(processed)
    try:
        store.append_processed_feedback(dicts)
    except OSError as e:
        log.warning(
            "feedback persistence failed (%s); dropping %d in-memory records — will retry next run",
            e,
            len(dicts),
        )
        return [], len(processed)
    return dicts, len(processed)


async def _maybe_update_soul(
    settings: Settings,
    store: StateStore,
    narrative_md: str | None,
    feedback_records: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    """Return (soul_md, user_id) or (None, None) if soul is disabled / unavailable."""
    if not settings.soul.enabled:
        return None, None

    try:
        user_id = user_id_from_jwt(settings.jiphyeonjeon.token)
    except Exception as e:
        log.warning("soul disabled this run (could not derive user_id): %s", e)
        return None, None

    # Fetch bookmarks once; soul needs newest-first order for delta diff.
    try:
        async with JiphyClient(settings.jiphyeonjeon) as jh:
            bookmarks = await jh.list_bookmarks()
    except Exception as e:
        log.warning("soul[%s]: could not fetch bookmarks: %s", user_id, e)
        return None, user_id

    last_bm_id = store.soul_last_bookmark_id(user_id)
    new_bookmarks = diff_new_bookmarks(bookmarks, last_bm_id)
    # Apply decay so the LLM sees recency weights on each new bookmark.
    if settings.decay.enabled and new_bookmarks:
        new_bookmarks = apply_decay(new_bookmarks, settings.decay.half_life_days)

    recent_picks = _recent_picks_from_ab_log(
        store,
        settings.soul.include_recent_picks_days,
    )
    existing_soul = store.load_soul(user_id)
    feedback_count = len(feedback_records or [])
    immediate_signals = bool(new_bookmarks or feedback_records)
    cadence_due = _soul_cadence_due(settings, store, user_id)

    log.info(
        "soul[%s]: %d new bookmarks, %d recent picks, %d feedback records, cadence_due=%s",
        user_id,
        len(new_bookmarks),
        len(recent_picks),
        feedback_count,
        cadence_due,
    )

    if existing_soul is not None and not immediate_signals and not cadence_due:
        log.info(
            "soul[%s]: skipping evolution until cadence (%d day[s]) or immediate signals",
            user_id,
            settings.soul.update_cadence_days,
        )
        return existing_soul, user_id

    soul_md = await update_soul(
        settings,
        store,
        user_id,
        narrative_md,
        new_bookmarks,
        recent_picks,
        user_feedback=feedback_records,
    )

    newest_bm_id: str | None = None
    if bookmarks:
        b0 = bookmarks[0]
        newest_bm_id = str(b0.get("id") or b0.get("paper_id") or "") or None
    store.bump_soul_update(user_id, newest_bm_id)

    return soul_md, user_id


async def run_pipeline(config_path: Path, force_profile: bool = False) -> Path:
    settings = load_settings(config_path)
    store = StateStore(settings.state_dir)

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log.info("=== paper-recommender pipeline start %s ===", started_at)

    json_profile, narrative_md = await build_profiles(settings, store, force=force_profile)
    log.info(
        "profile: %d keywords, %d interests, narrative=%s, source=%s",
        len(json_profile.get("keywords", [])),
        len(json_profile.get("interests", [])),
        bool(narrative_md),
        json_profile.get("source"),
    )

    feedback_records, _ = _collect_feedback(settings, store)
    if feedback_records:
        n_read = sum(1 for r in feedback_records if r.get("kind") == "read")
        n_dislike = len(feedback_records) - n_read
        log.info("feedback: %d new (%d read, %d dislike)", len(feedback_records), n_read, n_dislike)

    soul_md, user_id = await _maybe_update_soul(
        settings, store, narrative_md, feedback_records=feedback_records
    )
    if soul_md:
        log.info(
            "soul[%s]: ready (%d bytes)",
            user_id,
            len(soul_md.encode("utf-8")),
        )

    candidates = await gather_candidates(settings, store, json_profile)
    log.info("gathered %d candidates", len(candidates))

    suppress_terms = extract_suppress_keywords(soul_md) if soul_md else []
    # Fold dislike reasons in immediately so they apply this same run, even
    # before the next SOUL evolve picks them up into the Suppress section.
    extra_suppress = [
        r["reason"] for r in feedback_records
        if r.get("kind") == "dislike" and r.get("reason")
    ]
    suppress_terms = list(dict.fromkeys(suppress_terms + extra_suppress))
    candidates, dropped = _apply_suppress(candidates, suppress_terms)
    if suppress_terms:
        log.info(
            "suppress: %d terms (%s) → dropped %d candidates, %d remain",
            len(suppress_terms),
            ", ".join(suppress_terms[:5]) + ("…" if len(suppress_terms) > 5 else ""),
            dropped,
            len(candidates),
        )

    variants = _resolve_variants(
        settings,
        narrative_available=bool(narrative_md),
        soul_available=bool(soul_md),
    )
    log.info("rerank variants: %s", variants)

    variants_picks: dict[str, list[dict[str, Any]]] = {}
    variants_score_stats: dict[str, dict[str, float]] = {}
    for variant in variants:
        picks = await rerank_candidates(
            settings,
            json_profile,
            candidates,
            variant=variant,
            narrative_md=narrative_md,
            soul_md=soul_md,
        )
        variants_picks[variant] = picks
        stats = score_stats(picks)
        variants_score_stats[variant] = stats
        log.info(
            "rerank[%s,%s]: %d picks · score mean=%.2f std=%.2f spread=%.2f (top_k=%d)",
            variant,
            settings.rerank.scoring_mode,
            len(picks),
            stats["mean"],
            stats["std"],
            stats["spread"],
            settings.rerank.top_k,
        )

    artifact_dir = write_artifacts(
        settings,
        json_profile,
        narrative_md,
        soul_md,
        user_id,
        candidates,
        variants_picks,
    )
    log.info("wrote artifacts to %s", artifact_dir)

    all_picked: dict[str, dict[str, Any]] = {}
    for picks in variants_picks.values():
        for p in picks:
            all_picked[paper_key(p)] = p
    if all_picked:
        store.record_seen(list(all_picked.keys()))
        store.gc_seen(settings.seen.cooldown_days)

    variant_ids = {v: [paper_key(p) for p in picks] for v, picks in variants_picks.items()}
    jaccard = None
    if len(variants) == 2:
        a, b = variants
        jaccard = _jaccard(variant_ids[a], variant_ids[b])

    n_feedback_read = sum(1 for r in feedback_records if r.get("kind") == "read")
    n_feedback_dislike = len(feedback_records) - n_feedback_read

    store.append_ab_log(
        {
            "run_at": started_at,
            "user_id": user_id,
            "scoring_mode": settings.rerank.scoring_mode,
            "score_stats": variants_score_stats,
            "variants": {
                v: [
                    {
                        "paper_id": paper_key(p),
                        "title": p.get("title"),
                        "score": p.get("score"),
                        "rank": p.get("_rank"),
                    }
                    for p in picks
                ]
                for v, picks in variants_picks.items()
            },
            "jaccard": jaccard,
            "candidate_count": len(candidates),
            "candidates_suppressed": dropped,
            "soul_bytes": len(soul_md.encode("utf-8")) if soul_md else 0,
            "feedback": {"read": n_feedback_read, "dislike": n_feedback_dislike},
        }
    )

    store.append_run(
        {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_id": user_id,
            "scoring_mode": settings.rerank.scoring_mode,
            "candidate_count": len(candidates),
            "variants": {v: len(picks) for v, picks in variants_picks.items()},
            "score_stats": variants_score_stats,
            "jaccard": jaccard,
            "soul_bytes": len(soul_md.encode("utf-8")) if soul_md else 0,
            "feedback": {"read": n_feedback_read, "dislike": n_feedback_dislike},
            "decay_enabled": settings.decay.enabled,
            "artifact_dir": str(artifact_dir),
        }
    )
    return artifact_dir
