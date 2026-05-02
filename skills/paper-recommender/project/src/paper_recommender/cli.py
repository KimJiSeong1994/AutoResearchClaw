from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from paper_recommender.config import load_settings
from paper_recommender.daily_research import run_daily_research
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.llm import OpenClawLLM
from paper_recommender.pipeline import run_pipeline
from paper_recommender.weekly import run_weekly_report


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _cmd_doctor(config_path: Path) -> int:
    settings = load_settings(config_path)
    ok = True

    try:
        async with JiphyClient(settings.jiphyeonjeon) as jh:
            bms = await jh.list_bookmarks()
        print(f"jiphyeonjeon: ok ({len(bms)} bookmarks)")
    except Exception as e:
        print(f"jiphyeonjeon: FAIL — {e}")
        ok = False

    try:
        async with OpenClawLLM(settings.openclaw) as llm:
            reply = await llm.chat(
                messages=[
                    {"role": "system", "content": "reply with the single word: pong"},
                    {"role": "user", "content": "ping"},
                ],
                temperature=0,
            )
        print(f"openclaw: ok (reply={reply.strip()[:40]!r})")
    except Exception as e:
        print(f"openclaw: FAIL — {e}")
        ok = False

    return 0 if ok else 1


async def _cmd_run(config_path: Path, force_profile: bool) -> int:
    out = await run_pipeline(config_path, force_profile=force_profile)
    print(f"artifacts: {out}")
    return 0


async def _cmd_daily_research(config_path: Path, dry_run: bool) -> int:
    result = await run_daily_research(config_path, dry_run=dry_run)
    if dry_run:
        print(
            "daily-research dry-run: "
            f"sources={result.source_stats} "
            f"candidates={result.candidate_count} "
            f"clusters={result.cluster_count} "
            f"used_fallback={result.used_fallback}"
        )
    else:
        for p in result.paths_written:
            print(f"wrote: {p}")
        print(
            f"summary: candidates={result.candidate_count} "
            f"clusters={result.cluster_count} "
            f"deep={result.deep_success_count} "
            f"wall={result.wall_clock_sec:.0f}s"
        )
    return 0


async def _cmd_weekly_report(config_path: Path, force: bool, dry_run: bool) -> int:
    result = await run_weekly_report(config_path, force=force, dry_run=dry_run)
    if result.skipped:
        print(f"weekly report skipped: cadence not due (target={result.artifact_dir})")
    elif dry_run:
        print(
            "weekly report dry-run: "
            f"target={result.artifact_dir} queries={result.query_count} candidates={result.candidate_count}"
        )
    else:
        print(f"weekly report artifacts: {result.artifact_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper-recommender")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check jiphyeonjeon + openclaw connectivity")

    run_p = sub.add_parser("run", help="run full daily pipeline")
    run_p.add_argument("--force-profile", action="store_true", help="rebuild profile even if cached")

    weekly_p = sub.add_parser("weekly-report", help="generate SOUL-anchored weekly trend report")
    weekly_p.add_argument("--force", action="store_true", help="ignore weekly cadence and seen cooldown")
    weekly_p.add_argument("--dry-run", action="store_true", help="collect and synthesize without writing artifacts/state")

    dr_p = sub.add_parser("daily-research", help="multi-source daily research with deep bridge")
    dr_p.add_argument("--dry-run", action="store_true", help="skip deep bridge + file writes; report what would happen")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "doctor":
        return asyncio.run(_cmd_doctor(args.config))
    if args.command == "run":
        return asyncio.run(_cmd_run(args.config, args.force_profile))
    if args.command == "weekly-report":
        return asyncio.run(_cmd_weekly_report(args.config, args.force, args.dry_run))
    if args.command == "daily-research":
        return asyncio.run(_cmd_daily_research(args.config, args.dry_run))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
