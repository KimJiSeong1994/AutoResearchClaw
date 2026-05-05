from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .miner import AGENT_ID, PENDING_STATUS, REVIEWER_ID, clean_text, locked_jsonl_paths, read_jsonl, sanitize_url

Decision = Literal["approve", "reject", "hold"]
_APPROVED_STATUS = "approved_for_manual_links"
_APPROVED_BY_TAG = "approved-by-jiphyeonjeon-claw"
_VALID_DECISIONS: set[str] = {"approve", "reject", "hold"}


@dataclass(frozen=True)
class ReviewQueueItem:
    record: dict[str, Any]
    decision: dict[str, Any] | None

    @property
    def intake_id(self) -> str:
        return str(self.record.get("intake_id") or "")

    @property
    def decision_name(self) -> str:
        if not self.decision:
            return "pending"
        return str(self.decision.get("decision") or "pending")


def queue_items(queue_path: Path, decisions_path: Path) -> list[ReviewQueueItem]:
    with locked_jsonl_paths(queue_path, decisions_path):
        queue = read_jsonl(queue_path)
        latest = latest_decisions(decisions_path)
    return [ReviewQueueItem(record=row, decision=latest.get(str(row.get("intake_id") or ""))) for row in queue]


def show_item(queue_path: Path, decisions_path: Path, intake_id: str) -> ReviewQueueItem | None:
    for item in queue_items(queue_path, decisions_path):
        if item.intake_id == intake_id:
            return item
    return None


def record_decision(
    *,
    queue_path: Path,
    decisions_path: Path,
    intake_id: str,
    decision: Decision,
    reviewer: str = REVIEWER_ID,
    reason: str | None = None,
    decided_at: datetime | None = None,
) -> dict[str, Any]:
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"invalid decision: {decision}")
    with locked_jsonl_paths(queue_path, decisions_path):
        queue = {str(row.get("intake_id") or ""): row for row in read_jsonl(queue_path)}
        if intake_id not in queue:
            raise KeyError(f"unknown intake_id: {intake_id}")
        if not sanitize_url(queue[intake_id].get("url")):
            raise ValueError(f"queue record has unsafe url: {intake_id}")
        row = _decision_row(intake_id=intake_id, decision=decision, reviewer=reviewer, reason=reason, decided_at=decided_at)
        _append_jsonl_unlocked(decisions_path, row)
    return row


def latest_decisions(decisions_path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(decisions_path):
        decision = str(row.get("decision") or "")
        intake_id = str(row.get("intake_id") or "")
        if intake_id and decision in _VALID_DECISIONS:
            latest[intake_id] = row
    return latest


def export_approved_manual_links(
    *,
    queue_path: Path,
    decisions_path: Path,
    output_path: Path,
) -> list[dict[str, Any]]:
    with locked_jsonl_paths(queue_path, decisions_path, output_path):
        queue = read_jsonl(queue_path)
        latest = latest_decisions(decisions_path)
        items = [ReviewQueueItem(record=row, decision=latest.get(str(row.get("intake_id") or ""))) for row in queue]
        approved = [
            row
            for item in items
            if item.decision_name == "approve"
            for row in [_manual_link_row(item)]
            if row is not None
        ]
        _write_jsonl_atomic(output_path, approved)
    return approved


def _decision_row(
    *,
    intake_id: str,
    decision: str,
    reviewer: str,
    reason: str | None,
    decided_at: datetime | None,
) -> dict[str, Any]:
    now = decided_at or datetime.now(timezone.utc)
    timestamp = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    base = f"{intake_id}:{decision}:{reviewer}:{timestamp}:{clean_text(reason, limit=240)}"
    return {
        "decision_id": "review_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16],
        "intake_id": intake_id,
        "decision": decision,
        "reviewer": reviewer,
        "reason": clean_text(reason, limit=500),
        "decided_at": timestamp,
        "audit_source": "jiphyeonjeon_miner_review_cli",
    }


def _manual_link_row(item: ReviewQueueItem) -> dict[str, Any] | None:
    record = item.record
    decision = item.decision or {}
    url = sanitize_url(record.get("url"))
    title = clean_text(record.get("title"), limit=180)
    if not url or not title:
        return None
    tags = [str(tag) for tag in record.get("tags", []) if str(tag)]
    tags = [tag for tag in tags if tag != PENDING_STATUS]
    tags.extend(["manual-link", AGENT_ID, _APPROVED_STATUS, _APPROVED_BY_TAG])
    return {
        "title": title,
        "url": url,
        "summary": clean_text(record.get("summary"), limit=700),
        "published_at": clean_text(record.get("published_at"), limit=40),
        "source": str(record.get("source") or "discord_miner"),
        "tags": list(dict.fromkeys(tags)),
        "intake_id": item.intake_id,
        "review": {
            "owner": REVIEWER_ID,
            "decision": "approved",
            "source_decision": "approve",
            "reviewer": clean_text(decision.get("reviewer") or REVIEWER_ID, limit=80),
            "approved_at": clean_text(decision.get("decided_at"), limit=40),
            "audit_source": clean_text(decision.get("audit_source"), limit=120),
        },
    }


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        tmp = Path(fh.name)
    tmp.replace(path)


def _append_jsonl_unlocked(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
