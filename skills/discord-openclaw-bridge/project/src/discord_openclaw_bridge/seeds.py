"""Seed-driven periodic collection for the Miner pipeline.

Manages a persistent seed list (JSON) and last-seen state, expanding
each enabled seed URL into article links and recording them into the
intake/review queue via record_miner_link.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from discord_openclaw_bridge.article_metadata import ArticleMetadata
from discord_openclaw_bridge.article_metadata import fetch_article_metadata as _fam
from discord_openclaw_bridge.miner import (
    _is_alphaxiv_collection,
    _is_nature_articles_collection,
    _is_the_batch_collection,
    expand_collection_links,
    record_miner_link,
    sanitize_url,
)

logger = logging.getLogger(__name__)

DEFAULT_SEEDS_PATH = Path.home() / ".openclaw" / "workspace" / "config" / "miner-seeds.json"
DEFAULT_STATE_PATH = Path.home() / ".openclaw" / "workspace" / "state" / "miner-seeds-last-seen.json"


@dataclass(frozen=True)
class SeedEntry:
    url: str
    label: str
    cooldown_hours: int = 24
    enabled: bool = True
    max_links: int | None = None


@dataclass(frozen=True)
class SeedRunSummary:
    seed_url: str
    expanded_count: int
    accepted: int
    duplicate: int
    rejected: int
    skipped_cooldown: bool
    error: str | None = None


def _is_known_collection(url: str) -> bool:
    """Return True if *url* is a recognised expandable collection seed.

    Mirrors the check inside expand_collection_links so we can distinguish
    "collection returned 0 links (possible failure)" from "non-collection
    seed returned 0 links (expected/OK)".
    """
    return (
        _is_alphaxiv_collection(url)
        or _is_the_batch_collection(url)
        or _is_nature_articles_collection(url)
    )


def load_seeds(path: Path = DEFAULT_SEEDS_PATH) -> list[SeedEntry]:
    """Load and validate seed entries from JSON.

    Invalid entries are logged and skipped; a missing file returns [].
    """
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to parse seeds file %s: %s", path, exc)
        return []

    entries: list[SeedEntry] = []
    for item in raw.get("seeds", []):
        if not isinstance(item, dict):
            logger.warning("Skipping non-dict seed entry: %r", item)
            continue

        url = sanitize_url(item.get("url", ""))
        if not url:
            logger.warning("Skipping seed with invalid/missing url: %r", item)
            continue

        cooldown_hours = item.get("cooldown_hours", 24)
        if not isinstance(cooldown_hours, int) or cooldown_hours < 1:
            logger.warning("Skipping seed with invalid cooldown_hours: %r", item)
            continue

        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            logger.warning("Skipping seed with non-bool enabled: %r", item)
            continue

        max_links = item.get("max_links")
        if max_links is not None:
            if not isinstance(max_links, int) or not (1 <= max_links <= 50):
                logger.warning("Skipping seed with invalid max_links (must be int 1-50): %r", item)
                continue

        entries.append(
            SeedEntry(
                url=url,
                label=str(item.get("label", "")),
                cooldown_hours=cooldown_hours,
                enabled=enabled,
                max_links=max_links,
            )
        )
    return entries


def load_last_seen(path: Path = DEFAULT_STATE_PATH) -> dict[str, str]:
    """Load url->ISO8601 last-seen mapping.  Returns {} on missing or corrupt file."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning(
            "Failed to parse last_seen file %s: %s; starting fresh", path, exc
        )
    return {}


def save_last_seen(path: Path, mapping: dict[str, str]) -> None:
    """Atomically persist url->ISO8601 mapping via tmp+rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def expand_seeds(
    *,
    seeds: list[SeedEntry],
    intake_path: Path,
    review_queue_path: Path,
    state_path: Path = DEFAULT_STATE_PATH,
    now: datetime | None = None,
    fetch_html: Callable[[str], str] | None = None,
    fetch_metadata: Callable[[str], ArticleMetadata] | None = None,
) -> list[SeedRunSummary]:
    """Fetch each enabled seed, expand to article URLs, fetch metadata, and record.

    Skips seeds within their cooldown window.  Persists last_seen after each
    successful seed run.  Error isolation: a per-seed failure fills
    SeedRunSummary.error and continues to the next seed.

    Silent-fail guard: if a recognised collection seed expands to 0 links,
    last_seen is NOT updated so the next cron run retries.
    """
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    last_seen = load_last_seen(state_path)
    summaries: list[SeedRunSummary] = []

    if fetch_metadata is None:
        def _fetch_meta(url: str) -> ArticleMetadata:
            return _fam(url, fetch_html=fetch_html)
    else:
        _fetch_meta = fetch_metadata

    for seed in seeds:
        if not seed.enabled:
            summaries.append(
                SeedRunSummary(
                    seed_url=seed.url,
                    expanded_count=0,
                    accepted=0,
                    duplicate=0,
                    rejected=0,
                    skipped_cooldown=False,
                )
            )
            continue

        # ── Cooldown gate ────────────────────────────────────────────────────
        if seed.url in last_seen:
            try:
                last_ts = datetime.fromisoformat(last_seen[seed.url]).astimezone(timezone.utc)
                elapsed = current_time - last_ts
                if elapsed < timedelta(hours=seed.cooldown_hours):
                    summaries.append(
                        SeedRunSummary(
                            seed_url=seed.url,
                            expanded_count=0,
                            accepted=0,
                            duplicate=0,
                            rejected=0,
                            skipped_cooldown=True,
                        )
                    )
                    continue
            except (ValueError, OverflowError):
                # Bad stored timestamp; treat as never seen.
                pass

        # ── Collection expansion ─────────────────────────────────────────────
        try:
            links = expand_collection_links(seed.url)
            if seed.max_links is not None:
                links = links[: seed.max_links]
        except Exception as exc:
            logger.error("Failed to expand seed %s: %s", seed.url, exc)
            summaries.append(
                SeedRunSummary(
                    seed_url=seed.url,
                    expanded_count=0,
                    accepted=0,
                    duplicate=0,
                    rejected=0,
                    skipped_cooldown=False,
                    error=str(exc),
                )
            )
            continue

        # ── Silent-fail guard ────────────────────────────────────────────────
        # expand_collection_links swallows network/parse errors and returns [].
        # When a recognised collection yields 0 links we treat it as a transient
        # failure (selector drift, rate-limit, outage) and skip last_seen so the
        # next cron run retries rather than silently sleeping 24 h.
        if not links and _is_known_collection(seed.url):
            logger.warning(
                "Seed %s is a recognised collection but expanded to 0 links; "
                "marking empty_expansion — last_seen NOT updated",
                seed.url,
            )
            summaries.append(
                SeedRunSummary(
                    seed_url=seed.url,
                    expanded_count=0,
                    accepted=0,
                    duplicate=0,
                    rejected=0,
                    skipped_cooldown=False,
                    error="empty_expansion",
                )
            )
            continue

        # ── Per-article fetch & record ───────────────────────────────────────
        accepted = duplicate = rejected = 0
        first_article = True
        for article_url in links:
            if not first_article:
                time.sleep(1.0)
            first_article = False

            try:
                meta = _fetch_meta(article_url)
            except Exception as exc:
                logger.warning(
                    "Failed to fetch metadata for %s: %s; using blank metadata",
                    article_url,
                    exc,
                )
                meta = ArticleMetadata(url=article_url)

            result = record_miner_link(
                url=article_url,
                title=meta.title or None,
                summary=meta.summary or None,
                published_at=meta.published_at or None,
                intake_path=intake_path,
                review_queue_path=review_queue_path,
                created_at=current_time,
            )
            if result.accepted:
                accepted += 1
            elif result.duplicate:
                duplicate += 1
            elif result.rejected:
                rejected += 1

        # ── Persist last_seen on success ─────────────────────────────────────
        last_seen[seed.url] = current_time.replace(microsecond=0).isoformat()
        save_last_seen(state_path, last_seen)

        summaries.append(
            SeedRunSummary(
                seed_url=seed.url,
                expanded_count=len(links),
                accepted=accepted,
                duplicate=duplicate,
                rejected=rejected,
                skipped_cooldown=False,
            )
        )

    return summaries
