"""Personalization signal helpers shared across the pipeline.

Three responsibilities:
- ``decay_weight`` / ``apply_decay`` — exponential time decay on bookmarks.
- ``parse_feedback_markers`` — extract ``[read]`` / ``[dislike: ...]`` markers
  from a synced-back Obsidian recommendations note, with a frontmatter date
  guard so we never re-read stale notes.
- ``collect_feedback`` — orchestrate inbox scan + idempotency dedupe.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# --- time decay ---------------------------------------------------------

def decay_weight(
    timestamp_iso: str | None,
    half_life_days: int,
    today: date | None = None,
) -> float:
    """Exponential decay; returns a multiplier in [0.05, 1.0].

    ``half_life_days <= 0`` disables decay (everything weight 1.0).
    Missing or malformed timestamp also returns 1.0 — we never silently
    drop a bookmark just because the API omitted ``created_at``.
    """
    if not timestamp_iso or half_life_days <= 0:
        return 1.0
    if today is None:
        today = date.today()
    try:
        normalized = timestamp_iso.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        ts = parsed.date()
    except (ValueError, AttributeError, TypeError):
        return 1.0
    age = max(0, (today - ts).days)
    return max(0.05, 0.5 ** (age / half_life_days))


def apply_decay(
    bookmarks: list[dict[str, Any]],
    half_life_days: int,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Add ``_weight`` field and sort newest-effective first."""
    if half_life_days <= 0:
        return [{**bm, "_weight": 1.0} for bm in bookmarks]
    if today is None:
        today = date.today()
    weighted: list[dict[str, Any]] = []
    for bm in bookmarks:
        ts = bm.get("created_at") or bm.get("bookmarked_at")
        weighted.append({**bm, "_weight": decay_weight(ts, half_life_days, today)})
    weighted.sort(key=lambda b: b.get("_weight", 1.0), reverse=True)
    return weighted


# --- Obsidian feedback markers -----------------------------------------

@dataclass
class FeedbackRecord:
    paper_id: str | None
    title: str
    kind: str            # "read" | "dislike"
    reason: str | None   # None for "read"
    note_date: str       # ISO date of the source note

    def dedup_key(self) -> tuple:
        return (self.paper_id, self.title.strip().lower(), self.kind, self.reason, self.note_date)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "kind": self.kind,
            "reason": self.reason,
            "note_date": self.note_date,
        }


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
_DATE_RE = re.compile(r'^date:\s*"?(\d{4}-\d{2}-\d{2})"?', re.M)
_PICK_SPLIT_RE = re.compile(r"(?=^####?\s+\d+\.\s+)", re.M)
_PICK_HEADER_RE = re.compile(r"^####?\s+(\d+)\.\s+(.+?)\s*$", re.M)
_MARKER_RE = re.compile(
    r"\[(?:(read)|dislike\s*:\s*([^\]\n]+?))\]",
    re.IGNORECASE,
)
_PAPER_ID_PATTERNS = [
    re.compile(r"arxiv\.org/abs/([^\)\s\]]+)"),
    re.compile(r"jiphyeonjeon\.kr/papers/([^\)\s\]]+)"),
]


def parse_feedback_markers(
    md_text: str,
    *,
    today: date,
    lookback_days: int,
) -> list[FeedbackRecord]:
    """Parse `[read]` / `[dislike: reason]` markers from a daily note.

    Returns [] for any note whose `date:` frontmatter is missing, malformed,
    or older than ``lookback_days``. The lookback is the primary defense
    against re-injecting old markers (the feedback-loop concern).
    """
    if not md_text:
        return []
    fm = _FRONTMATTER_RE.match(md_text)
    if not fm:
        return []
    date_m = _DATE_RE.search(fm.group(1))
    if not date_m:
        return []
    try:
        note_date = date.fromisoformat(date_m.group(1))
    except ValueError:
        return []
    if (today - note_date).days > lookback_days or note_date > today:
        return []

    body = md_text[fm.end():]
    records: list[FeedbackRecord] = []
    for section in _PICK_SPLIT_RE.split(body):
        header = _PICK_HEADER_RE.match(section)
        if not header:
            continue
        title = header.group(2).strip()
        paper_id: str | None = None
        for pat in _PAPER_ID_PATTERNS:
            m = pat.search(section)
            if m:
                paper_id = m.group(1).strip()
                break

        for marker in _MARKER_RE.finditer(section):
            if marker.group(1):  # "read"
                records.append(FeedbackRecord(
                    paper_id=paper_id,
                    title=title,
                    kind="read",
                    reason=None,
                    note_date=note_date.isoformat(),
                ))
            else:
                reason = (marker.group(2) or "").strip()
                if not reason:
                    continue
                records.append(FeedbackRecord(
                    paper_id=paper_id,
                    title=title,
                    kind="dislike",
                    reason=reason[:200],
                    note_date=note_date.isoformat(),
                ))
    return records


def collect_feedback(
    inbox_dir: Path,
    *,
    today: date,
    lookback_days: int,
    max_file_bytes: int,
    already_processed: set[tuple],
) -> list[FeedbackRecord]:
    """Scan the feedback inbox, parse, dedup against ``already_processed``.

    The inbox is expected to be populated by ``sync-results.sh`` with one
    file per recent day, name pattern ``YYYY-MM-DD.md``. Files larger than
    ``max_file_bytes`` are skipped with a warning.
    """
    if not inbox_dir.exists():
        return []
    new_records: list[FeedbackRecord] = []
    for path in sorted(inbox_dir.glob("*.md")):
        # Read first, then size-check the actual bytes — eliminates the TOCTOU
        # window between stat() and open() where a file could be replaced.
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            log.warning("feedback inbox read failed for %s: %s", path, e)
            continue
        if len(text.encode("utf-8")) > max_file_bytes:
            log.warning(
                "feedback inbox skipping oversized file %s (%d bytes)",
                path,
                len(text.encode("utf-8")),
            )
            continue
        for rec in parse_feedback_markers(text, today=today, lookback_days=lookback_days):
            if rec.dedup_key() in already_processed:
                continue
            new_records.append(rec)
            already_processed.add(rec.dedup_key())
    return new_records
