from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from paper_recommender.config import load_settings
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.llm import OpenClawLLM
from paper_recommender.pipeline import run_pipeline


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paper-recommender")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check jiphyeonjeon + openclaw connectivity")

    run_p = sub.add_parser("run", help="run full daily pipeline")
    run_p.add_argument("--force-profile", action="store_true", help="rebuild profile even if cached")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.command == "doctor":
        return asyncio.run(_cmd_doctor(args.config))
    if args.command == "run":
        return asyncio.run(_cmd_run(args.config, args.force_profile))
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
