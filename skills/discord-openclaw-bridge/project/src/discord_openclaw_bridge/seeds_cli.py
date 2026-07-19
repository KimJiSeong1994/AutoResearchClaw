"""CLI entrypoint for the miner seed expansion pipeline.

Usage:
    discord-openclaw-miner-seeds           # runs and persists last_seen
    discord-openclaw-miner-seeds --once    # runs once, persists last_seen
    discord-openclaw-miner-seeds --dry-run # validates seeds.json, no network/write

The CLI loads ``project/.env`` (cwd-relative) before resolving default paths so
``JIPHYEONJEON_MINER_INTAKE_PATH`` / ``JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH``
set there flow through to ``--intake-path`` / ``--review-queue-path`` defaults.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from discord_openclaw_bridge.config import _load_dotenv
from discord_openclaw_bridge.article_metadata import fetch_article_metadata
from discord_openclaw_bridge.seeds import (
    DEFAULT_SEEDS_PATH,
    DEFAULT_STATE_PATH,
    SeedRunSummary,
    expand_seeds,
    load_seeds,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# Errors that the operator should treat as transient / self-healing rather than
# as a service outage. They surface in the Discord report under a ⚠️ prefix
# instead of 🚨, so a daily Nature selector drift does not desensitise the
# operator to genuine network or parser failures.
TRANSIENT_ERROR_TAGS: frozenset[str] = frozenset({"empty_expansion"})



def _resolve_default_paths() -> dict[str, Path]:
    """Compute production-aligned default paths from env vars at call time.

    Called *after* ``_load_dotenv`` so values from ``project/.env`` win over
    the workspace-rooted fallbacks. Keeping this as a function (rather than
    module-level constants) lets the CLI re-resolve when the env changes
    between import and ``main()`` — important for cron runs where the
    runner ``cd``s into the project before invoking the console script.
    """
    workspace = Path.home() / ".openclaw" / "workspace"
    return {
        "intake": Path(
            os.environ.get(
                "JIPHYEONJEON_MINER_INTAKE_PATH",
                str(workspace / "intake" / "jiphyeonjeon-miner" / "links.jsonl"),
            )
        ).expanduser(),
        "review_queue": Path(
            os.environ.get(
                "JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH",
                str(workspace / "review" / "jiphyeonjeon-claw" / "link-review-queue.jsonl"),
            )
        ).expanduser(),
        "status": Path(
            os.environ.get(
                "MINER_SEEDS_STATUS_PATH",
                str(workspace / "state" / "miner-seeds-last-status.json"),
            )
        ).expanduser(),
    }


def _write_last_status(
    path: Path,
    *,
    summaries: list[SeedRunSummary],
    run_at: str,
    duration_sec: float,
    intake_path: Path,
    review_queue_path: Path,
) -> None:
    """Atomically write the last-run status JSON for downstream reporters.

    ``seeds_with_errors`` counts only non-transient failures so a daily
    selector drift no longer raises 🚨 in the Discord report. Transient
    cases (currently just ``empty_expansion``) move to ``seeds_with_warnings``
    so the reporter can pick a calmer ⚠️ prefix.
    """

    real_errors = sum(
        1 for s in summaries if s.error and s.error not in TRANSIENT_ERROR_TAGS
    )
    transient_warnings = sum(
        1 for s in summaries if s.error in TRANSIENT_ERROR_TAGS
    )
    payload = {
        "run_at": run_at,
        "duration_sec": round(duration_sec, 2),
        "seeds_total": len(summaries),
        "seeds_processed": sum(1 for s in summaries if not s.skipped_cooldown and not s.error),
        "seeds_skipped_cooldown": sum(1 for s in summaries if s.skipped_cooldown),
        "seeds_with_errors": real_errors,
        "seeds_with_warnings": transient_warnings,
        "total_expanded": sum(s.expanded_count for s in summaries),
        "total_accepted": sum(s.accepted for s in summaries),
        "total_duplicate": sum(s.duplicate for s in summaries),
        "total_rejected": sum(s.rejected for s in summaries),
        "intake_path": str(intake_path),
        "review_queue_path": str(review_queue_path),
        "summaries": [
            {
                "seed_url": s.seed_url,
                "expanded_count": s.expanded_count,
                "accepted": s.accepted,
                "duplicate": s.duplicate,
                "rejected": s.rejected,
                "skipped_cooldown": s.skipped_cooldown,
                "error": s.error,
            }
            for s in summaries
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _build_parser(defaults: dict[str, Path]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-miner-seeds",
        description="Expand registered seed URLs into miner intake records.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate seeds.json and print what would run; make no network calls or writes.",
    )
    group.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run once, respecting cooldown and persisting last_seen."
            " (Same as default; kept for explicitness.)"
        ),
    )
    parser.add_argument(
        "--seeds-path",
        type=Path,
        default=DEFAULT_SEEDS_PATH,
        help=f"Path to miner-seeds.json (default: {DEFAULT_SEEDS_PATH})",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"Path to last-seen state file (default: {DEFAULT_STATE_PATH})",
    )
    parser.add_argument(
        "--intake-path",
        type=Path,
        default=defaults["intake"],
        help=f"Path to intake JSONL (default: {defaults['intake']})",
    )
    parser.add_argument(
        "--review-queue-path",
        type=Path,
        default=defaults["review_queue"],
        help=f"Path to review queue JSONL (default: {defaults['review_queue']})",
    )
    parser.add_argument(
        "--status-path",
        type=Path,
        default=defaults["status"],
        help=f"Path to last-status JSON (default: {defaults['status']})",
    )
    return parser


def _fetch_meta_wrapper(url: str):
    """Pass-through to fetch_article_metadata (ArticleMetadata is now shared)."""
    return fetch_article_metadata(url)


def _log_summaries(summaries: list[SeedRunSummary]) -> None:
    for s in summaries:
        if s.error and s.error not in TRANSIENT_ERROR_TAGS:
            logger.error("seed %s ERROR: %s", s.seed_url, s.error)
        elif s.error:
            logger.warning("seed %s WARN: %s", s.seed_url, s.error)
        elif s.skipped_cooldown:
            logger.info("seed %s SKIPPED (cooldown)", s.seed_url)
        else:
            logger.info(
                "seed %s expanded=%d accepted=%d duplicate=%d rejected=%d",
                s.seed_url,
                s.expanded_count,
                s.accepted,
                s.duplicate,
                s.rejected,
            )


def main(argv: list[str] | None = None) -> None:
    _load_dotenv(Path.cwd() / ".env")
    defaults = _resolve_default_paths()
    parser = _build_parser(defaults)
    args = parser.parse_args(argv)

    seeds = load_seeds(args.seeds_path)
    if not seeds:
        logger.warning("No seeds loaded from %s — nothing to do.", args.seeds_path)
        return

    logger.info("Loaded %d seed(s) from %s", len(seeds), args.seeds_path)

    if args.dry_run:
        for seed in seeds:
            logger.info(
                "[dry-run] seed url=%s label=%r enabled=%s cooldown_hours=%d max_links=%s",
                seed.url,
                seed.label,
                seed.enabled,
                seed.cooldown_hours,
                seed.max_links,
            )
        logger.info("[dry-run] done — no network calls or writes performed")
        return

    started = time.monotonic()
    run_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    summaries = expand_seeds(
        seeds=seeds,
        intake_path=args.intake_path,
        review_queue_path=args.review_queue_path,
        state_path=args.state_path,
        fetch_metadata=_fetch_meta_wrapper,
    )
    duration_sec = time.monotonic() - started
    _log_summaries(summaries)

    _write_last_status(
        args.status_path,
        summaries=summaries,
        run_at=run_at,
        duration_sec=duration_sec,
        intake_path=args.intake_path,
        review_queue_path=args.review_queue_path,
    )

    real_errors = sum(
        1 for s in summaries if s.error and s.error not in TRANSIENT_ERROR_TAGS
    )
    transient_warnings = sum(
        1 for s in summaries if s.error in TRANSIENT_ERROR_TAGS
    )
    total_accepted = sum(s.accepted for s in summaries)
    logger.info(
        "Run complete: %d seed(s), %d accepted, %d error(s), %d warning(s) in %.1fs (status=%s)",
        len(summaries),
        total_accepted,
        real_errors,
        transient_warnings,
        duration_sec,
        args.status_path,
    )
    # Transient warnings should not break the cron — the next firing retries.
    if real_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
