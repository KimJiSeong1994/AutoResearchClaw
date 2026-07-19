"""집현전-클로 verdicts on 집현전-여행자 source candidates.

`bot.py` has long described the flow "여행자 → 후보 출처 발굴 → 클로 →
approve/reject/hold append-only decision", but no code implemented the verdict
write. Candidates were written `pending_source_review` and nothing ever moved
them, so there was no record of whether a discovery was any good.

This mirrors the miner link review (`review.record_decision`) rather than
extending it. The two are categorically different objects: the miner queue holds
*content* — approving an article means "this article is good" — while this queue
holds *sources* — approving a feed means "collect from here from now on". One
verb over both would be ambiguous at the point where a consumer acts on it.

Decisions are append-only and never mutate the candidate row, so history is
preserved and a verdict can be revised by recording a newer one. `approve` and
`reject` are terminal and drop the candidate out of the daily report; `hold` is
an explicit "show me again", so held candidates keep appearing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .miner import REVIEWER_ID, clean_text, locked_jsonl_paths, sanitize_url
from .review import _append_jsonl_unlocked
from .traveler import default_source_queue_path

LOG = logging.getLogger(__name__)

SourceDecision = Literal["approve", "reject", "hold"]
VALID_DECISIONS: set[str] = {"approve", "reject", "hold"}
# `hold` deliberately excluded: it means "revisit", so the candidate stays visible.
TERMINAL_DECISIONS: frozenset[str] = frozenset({"approve", "reject"})
SCHEMA_VERSION = "traveler-source-decision.v1"


def default_source_decisions_path() -> Path:
    return default_source_queue_path().parent / "source-review-decisions.jsonl"


def _decision_row(*, candidate_id: str, url: str, decision: str, reviewer: str, reason: str | None, decided_at: datetime | None) -> dict[str, Any]:
    now = decided_at or datetime.now(timezone.utc)
    timestamp = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    basis = f"{candidate_id}:{decision}:{reviewer}:{timestamp}:{clean_text(reason, limit=240)}"
    return {
        "schema_version": SCHEMA_VERSION,
        "decision_id": "source_review_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16],
        "candidate_id": candidate_id,
        "url": url,
        "decision": decision,
        "reviewer": reviewer,
        "reason": clean_text(reason, limit=240),
        "decided_at": timestamp,
    }


def latest_source_decisions(decisions_path: Path) -> dict[str, dict[str, Any]]:
    """Newest valid decision per candidate, so a verdict can be revised."""
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl_rows(decisions_path):
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id and str(row.get("decision") or "") in VALID_DECISIONS:
            latest[candidate_id] = row
    return latest


def decided_candidate_ids(decisions_path: Path) -> set[str]:
    """Candidates whose review is finished — these leave the daily report."""
    return {
        candidate_id
        for candidate_id, row in latest_source_decisions(decisions_path).items()
        if str(row.get("decision") or "") in TERMINAL_DECISIONS
    }


def record_source_decision(
    *,
    queue_path: Path,
    decisions_path: Path,
    candidate_id: str,
    decision: SourceDecision,
    reviewer: str = REVIEWER_ID,
    reason: str | None = None,
    decided_at: datetime | None = None,
) -> dict[str, Any]:
    if decision not in VALID_DECISIONS:
        raise ValueError(f"invalid decision: {decision}")
    with locked_jsonl_paths(queue_path, decisions_path):
        queue = {str(row.get("candidate_id") or ""): row for row in _read_jsonl_rows(queue_path)}
        if candidate_id not in queue:
            raise KeyError(f"unknown candidate_id: {candidate_id}")
        url = sanitize_url(queue[candidate_id].get("url"))
        if not url:
            raise ValueError(f"queue record has unsafe url: {candidate_id}")
        row = _decision_row(
            candidate_id=candidate_id,
            url=url,
            decision=decision,
            reviewer=reviewer,
            reason=reason,
            decided_at=decided_at,
        )
        _append_jsonl_unlocked(decisions_path, row)
    return row


def pending_candidates(queue_path: Path, decisions_path: Path) -> list[dict[str, Any]]:
    """Undecided candidates, newest first, with any non-terminal verdict attached."""
    latest = latest_source_decisions(decisions_path)
    terminal = decided_candidate_ids(decisions_path)
    pending: list[dict[str, Any]] = []
    for row in _read_jsonl_rows(queue_path):
        candidate_id = str(row.get("candidate_id") or "")
        if not candidate_id or candidate_id in terminal:
            continue
        decision = latest.get(candidate_id)
        pending.append({**row, "current_decision": str(decision.get("decision")) if decision else "pending"})
    pending.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return pending


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-traveler-review",
        description="집현전-클로 review of Traveler source candidates (append-only verdicts).",
    )
    parser.add_argument("--queue", type=Path, default=None, help="Source-candidate queue JSONL.")
    parser.add_argument("--decisions", type=Path, default=None, help="Source-review decisions JSONL.")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="Show candidates still awaiting a verdict.")
    listing.add_argument("--limit", type=int, default=20)

    decide = sub.add_parser("decide", help="Record approve/reject/hold for one candidate.")
    decide.add_argument("candidate_id")
    decide.add_argument("decision", choices=sorted(VALID_DECISIONS))
    decide.add_argument("--reason", default=None)
    decide.add_argument("--reviewer", default=REVIEWER_ID)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    queue = (args.queue or default_source_queue_path()).expanduser()
    decisions = (args.decisions or default_source_decisions_path()).expanduser()

    if args.command == "list":
        rows = pending_candidates(queue, decisions)[: max(0, args.limit)]
        if not rows:
            print("no source candidates awaiting review")
            return 0
        for row in rows:
            print(
                json.dumps(
                    {
                        "candidate_id": row.get("candidate_id"),
                        "title": row.get("title"),
                        "url": row.get("url"),
                        "source_type": row.get("source_type"),
                        "created_at": row.get("created_at"),
                        "current_decision": row.get("current_decision"),
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    try:
        row = record_source_decision(
            queue_path=queue,
            decisions_path=decisions,
            candidate_id=args.candidate_id,
            decision=args.decision,
            reviewer=args.reviewer,
            reason=args.reason,
        )
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
