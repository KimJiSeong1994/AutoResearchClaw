"""Read-only review queue optimizer report for 집현전-클로.

The optimizer is deliberately report-only.  It never writes review decisions,
rewrites queues, or mutates approved exports; it only returns duplicate, stale,
and priority signals that help an operator schedule review work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .miner import read_jsonl, sanitize_url
from .review import latest_decisions
from .review_cli import DEFAULT_DECISIONS, DEFAULT_REVIEW_QUEUE

DEFAULT_MAX_AGE_DAYS = 7.0
TRACKING_PREFIXES = ("utm_",)
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref"}


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_url(url: str) -> str:
    safe_url = sanitize_url(url)
    if not safe_url:
        return ""
    parsed = urlsplit(safe_url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in TRACKING_PARAMS and not any(key.startswith(prefix) for prefix in TRACKING_PREFIXES)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, urlencode(query, doseq=True), ""))


def _decision_for(row: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> str:
    intake_id = str(row.get("intake_id") or "")
    decision = decisions.get(intake_id)
    return str((decision or {}).get("decision") or "pending")


def _pending_rows(queue_rows: list[dict[str, Any]], decisions: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in queue_rows if _decision_for(row, decisions) == "pending"]


def _duplicate_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        normalized = normalize_url(str(row.get("url") or ""))
        if normalized:
            clusters[normalized].append(row)
    out: list[dict[str, Any]] = []
    for normalized, cluster in sorted(clusters.items()):
        if len(cluster) < 2:
            continue
        out.append(
            {
                "normalized_url": normalized,
                "count": len(cluster),
                "intake_ids": [str(row.get("intake_id") or "") for row in cluster],
                "raw_urls": list(dict.fromkeys(str(row.get("url") or "") for row in cluster)),
            }
        )
    return out


def _stale_items(rows: list[dict[str, Any]], *, now: datetime, max_age_days: float) -> list[dict[str, Any]]:
    stale: list[dict[str, Any]] = []
    for row in rows:
        created_at = _parse_utc(row.get("created_at"))
        if created_at is None:
            continue
        age_days = (now - created_at).total_seconds() / 86400
        if age_days > max_age_days:
            stale.append(
                {
                    "intake_id": str(row.get("intake_id") or ""),
                    "url": str(row.get("url") or ""),
                    "created_at": created_at.isoformat().replace("+00:00", "Z"),
                    "age_days": round(age_days, 2),
                }
            )
    return sorted(stale, key=lambda item: item["age_days"], reverse=True)


def _priority_recommendations(rows: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for row in rows:
        reasons: list[str] = []
        score = 0
        text = " ".join(
            str(row.get(key) or "")
            for key in ("title", "summary", "source", "intake_source")
        ).lower()
        if any(term in text for term in ("research", "paper", "arxiv", "openreview", "benchmark", "evaluation")):
            score += 3
            reasons.append("research_or_benchmark_signal")
        created_at = _parse_utc(row.get("created_at"))
        if created_at is not None:
            age_days = (now - created_at).total_seconds() / 86400
            if age_days > 3:
                score += 2
                reasons.append("aging_pending_item")
        discord = row.get("discord") if isinstance(row.get("discord"), dict) else {}
        if str(discord.get("user_id") or ""):
            score += 1
            reasons.append("traceable_discord_origin")
        if score:
            recommendations.append(
                {
                    "intake_id": str(row.get("intake_id") or ""),
                    "url": str(row.get("url") or ""),
                    "score": score,
                    "reasons": reasons,
                }
            )
    return sorted(recommendations, key=lambda item: (-int(item["score"]), item["intake_id"]))[:20]


def build_optimizer_report(
    *,
    queue_rows: list[dict[str, Any]],
    decisions_rows: list[dict[str, Any]],
    queue_path: str,
    decisions_path: str,
    now: datetime | None = None,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
) -> dict[str, Any]:
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    decisions = {
        str(row.get("intake_id") or ""): row
        for row in decisions_rows
        if str(row.get("decision") or "") in {"approve", "reject", "hold"}
    }
    pending = _pending_rows(queue_rows, decisions)
    return {
        "agent_id": "review-queue-optimizer",
        "generated_at": current_time.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "queue_snapshot": {
            "queue_path": queue_path,
            "decisions_path": decisions_path,
            "total_rows": len(queue_rows),
            "pending_rows": len(pending),
            "decided_rows": len(queue_rows) - len(pending),
            "snapshot_hash": hashlib.sha256(
                json.dumps(queue_rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16],
        },
        "duplicate_candidates": _duplicate_candidates(pending),
        "stale_items": _stale_items(pending, now=current_time, max_age_days=max_age_days),
        "priority_recommendations": _priority_recommendations(pending, now=current_time),
        "no_mutation": True,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-review-queue-optimizer",
        description="Generate a read-only duplicate/stale/priority report for the Jiphyeonjeon review queue.",
    )
    parser.add_argument("--queue", type=Path, default=DEFAULT_REVIEW_QUEUE)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--max-age-days", type=float, default=float(os.getenv("JIPHYEONJEON_REVIEW_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    queue_path = args.queue.expanduser()
    decisions_path = args.decisions.expanduser()
    report = build_optimizer_report(
        queue_rows=read_jsonl(queue_path),
        decisions_rows=list(latest_decisions(decisions_path).values()),
        queue_path=str(queue_path),
        decisions_path=str(decisions_path),
        max_age_days=args.max_age_days,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
