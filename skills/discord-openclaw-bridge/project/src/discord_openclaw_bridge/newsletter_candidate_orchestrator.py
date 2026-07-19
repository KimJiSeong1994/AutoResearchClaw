"""Create editorial-review candidates from Claw-approved links.

This module intentionally does not publish, edit newsletter archives, or write
card-news sources.  It only creates a separate candidate artifact whose rows
remain ``needs_editorial_review`` until a downstream human/operator workflow
chooses to use them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._shared import _write_jsonl_atomic
from .miner import clean_text, read_jsonl, sanitize_url
from .review import latest_decisions
from .review_cli import DEFAULT_DECISIONS, DEFAULT_EXPORT, DEFAULT_REVIEW_QUEUE

DEFAULT_CANDIDATE_PATH = (
    Path.home() / ".openclaw" / "workspace" / "review" / "newsletter-candidates" / "candidate-review.jsonl"
)


def _row_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]



def build_newsletter_candidates(
    *,
    approved_rows: list[dict[str, Any]],
    queue_rows: list[dict[str, Any]],
    decisions_rows: list[dict[str, Any]],
    generated_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return candidate-review rows for approved links only."""

    generated = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    queue_by_id = {str(row.get("intake_id") or ""): row for row in queue_rows}
    latest = {
        str(row.get("intake_id") or ""): row
        for row in decisions_rows
        if str(row.get("decision") or "") in {"approve", "reject", "hold"}
    }
    candidates: list[dict[str, Any]] = []
    for approved in approved_rows:
        intake_id = str(approved.get("intake_id") or "")
        decision = latest.get(intake_id)
        if not intake_id or not decision or decision.get("decision") != "approve":
            continue
        source = queue_by_id.get(intake_id, {})
        approved_url = sanitize_url(approved.get("url") or "")
        source_url = sanitize_url(source.get("url") or "")
        if not approved_url or approved_url != source_url:
            continue
        review = approved.get("review") if isinstance(approved.get("review"), dict) else {}
        tags = {str(tag) for tag in approved.get("tags", []) if str(tag)}
        if review.get("source_decision") != "approve" or "approved-by-jiphyeonjeon-claw" not in tags:
            continue
        url = source_url
        if not url:
            continue
        candidate = {
            "agent_id": "newsletter-candidate-orchestrator",
            "candidate_id": "newsletter_candidate_" + _row_hash({"intake_id": intake_id, "url": url}),
            "candidate_status": "needs_editorial_review",
            "generated_at": generated,
            "title": clean_text(approved.get("title") or source.get("title"), limit=180),
            "url": url,
            "summary": clean_text(approved.get("summary") or source.get("summary"), limit=700),
            "published_at": clean_text(approved.get("published_at") or source.get("published_at"), limit=40),
            "source_intake_id": intake_id,
            "source_decision_id": str(decision.get("decision_id") or ""),
            "approved_export_row_hash": _row_hash(approved),
            "recommended_next_action": "editorial_review_before_newsletter_or_card_news",
            "safety": {
                "approval_source": "jiphyeonjeon-claw",
                "publish_ready": False,
                "writes_newsletter_archive": False,
                "writes_card_news_source": False,
            },
        }
        candidates.append(candidate)
    candidates.sort(key=lambda row: (str(row.get("title") or "").lower(), str(row.get("url") or "")))
    return candidates


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-newsletter-candidate-orchestrator",
        description="Create separate editorial-review candidates from Claw-approved manual links.",
    )
    parser.add_argument("--queue", type=Path, default=DEFAULT_REVIEW_QUEUE)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--approved", type=Path, default=DEFAULT_EXPORT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(os.getenv("JIPHYEONJEON_NEWSLETTER_CANDIDATE_PATH", str(DEFAULT_CANDIDATE_PATH))),
    )
    parser.add_argument("--dry-run", action="store_true", help="render candidates without writing the output artifact")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_path = args.output.expanduser()
    candidates = build_newsletter_candidates(
        approved_rows=read_jsonl(args.approved.expanduser()),
        queue_rows=read_jsonl(args.queue.expanduser()),
        decisions_rows=list(latest_decisions(args.decisions.expanduser()).values()),
    )
    payload = {
        "agent_id": "newsletter-candidate-orchestrator",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "candidate_count": len(candidates),
        "output_path": str(output_path),
        "dry_run": bool(args.dry_run),
        "candidates": candidates,
    }
    if not args.dry_run:
        _write_jsonl_atomic(output_path, candidates)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
