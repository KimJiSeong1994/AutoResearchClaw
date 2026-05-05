from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .review import export_approved_manual_links, queue_items, record_decision, show_item

_DEFAULT_REVIEW_ROOT = Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-claw"
DEFAULT_REVIEW_QUEUE = Path(
    os.getenv("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", str(_DEFAULT_REVIEW_ROOT / "link-review-queue.jsonl"))
)
DEFAULT_DECISIONS = Path(
    os.getenv("JIPHYEONJEON_MINER_DECISIONS_PATH", str(_DEFAULT_REVIEW_ROOT / "link-review-decisions.jsonl"))
)
DEFAULT_EXPORT = Path(
    os.getenv(
        "JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH",
        str(Path.home() / ".openclaw" / "workspace" / "manual_links" / "approved-manual-links.jsonl"),
    )
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the 집현전-광부 review queue.")
    parser.add_argument("--queue", type=Path, default=DEFAULT_REVIEW_QUEUE, help="pending review queue JSONL")
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS, help="audit decision JSONL")
    sub = parser.add_subparsers(dest="command", required=True)

    list_p = sub.add_parser("list", help="list review queue items")
    list_p.add_argument("--decision", choices=["pending", "approve", "reject", "hold"], help="filter by latest decision")

    show_p = sub.add_parser("show", help="show one queue item")
    show_p.add_argument("intake_id")

    for name in ("approve", "reject", "hold"):
        p = sub.add_parser(name, help=f"append a {name} audit decision")
        p.add_argument("intake_id")
        p.add_argument("--reviewer", default="jiphyeonjeon-claw")
        p.add_argument("--reason", default="")

    export_p = sub.add_parser("export", help="write approved-only manual_links JSONL")
    export_p.add_argument("--output", type=Path, default=DEFAULT_EXPORT)
    export_p.add_argument(
        "--enrich",
        dest="enrich",
        action="store_true",
        default=True,
        help="fetch public HTML metadata to improve empty/fallback title, summary, and article date (default)",
    )
    export_p.add_argument("--no-enrich", dest="enrich", action="store_false", help="skip public HTML metadata fetch")
    export_p.add_argument("--metadata-timeout-sec", type=float, default=5.0, help="per-link metadata fetch timeout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            items = queue_items(args.queue.expanduser(), args.decisions.expanduser())
            for item in items:
                if args.decision and item.decision_name != args.decision:
                    continue
                row = item.record
                print(f"{item.intake_id}\t{item.decision_name}\t{row.get('title', '')}\t{row.get('url', '')}")
            return 0
        if args.command == "show":
            item = show_item(args.queue.expanduser(), args.decisions.expanduser(), args.intake_id)
            if item is None:
                print(f"unknown intake_id: {args.intake_id}", file=sys.stderr)
                return 1
            print(json.dumps({"record": item.record, "latest_decision": item.decision}, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"approve", "reject", "hold"}:
            row = record_decision(
                queue_path=args.queue.expanduser(),
                decisions_path=args.decisions.expanduser(),
                intake_id=args.intake_id,
                decision=args.command,
                reviewer=args.reviewer,
                reason=args.reason,
            )
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
            return 0
        if args.command == "export":
            rows = export_approved_manual_links(
                queue_path=args.queue.expanduser(),
                decisions_path=args.decisions.expanduser(),
                output_path=args.output.expanduser(),
                enrich=args.enrich,
                metadata_timeout_sec=args.metadata_timeout_sec,
            )
            print(f"exported {len(rows)} approved manual links to {args.output.expanduser()}")
            return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
