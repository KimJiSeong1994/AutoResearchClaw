"""CLI entrypoint for the miner seed expansion pipeline.

Usage:
    discord-openclaw-miner-seeds           # runs and persists last_seen
    discord-openclaw-miner-seeds --once    # runs once, persists last_seen
    discord-openclaw-miner-seeds --dry-run # validates seeds.json, no network/write
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

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

_WORKSPACE = Path.home() / ".openclaw" / "workspace"
_DEFAULT_INTAKE_PATH = _WORKSPACE / "intake" / "miner-links.jsonl"
_DEFAULT_REVIEW_QUEUE_PATH = _WORKSPACE / "review" / "queue.jsonl"


def _build_parser() -> argparse.ArgumentParser:
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
        default=_DEFAULT_INTAKE_PATH,
        help=f"Path to intake JSONL (default: {_DEFAULT_INTAKE_PATH})",
    )
    parser.add_argument(
        "--review-queue-path",
        type=Path,
        default=_DEFAULT_REVIEW_QUEUE_PATH,
        help=f"Path to review queue JSONL (default: {_DEFAULT_REVIEW_QUEUE_PATH})",
    )
    return parser


def _fetch_meta_wrapper(url: str):
    """Pass-through to fetch_article_metadata (ArticleMetadata is now shared)."""
    return fetch_article_metadata(url)


def _log_summaries(summaries: list[SeedRunSummary]) -> None:
    for s in summaries:
        if s.error:
            logger.error("seed %s ERROR: %s", s.seed_url, s.error)
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
    parser = _build_parser()
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

    summaries = expand_seeds(
        seeds=seeds,
        intake_path=args.intake_path,
        review_queue_path=args.review_queue_path,
        state_path=args.state_path,
        fetch_metadata=_fetch_meta_wrapper,
    )
    _log_summaries(summaries)

    errors = sum(1 for s in summaries if s.error)
    total_accepted = sum(s.accepted for s in summaries)
    logger.info(
        "Run complete: %d seed(s), %d accepted, %d error(s)",
        len(summaries),
        total_accepted,
        errors,
    )
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
