from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from ._shared import _write_jsonl_atomic
from .miner import AGENT_ID, PENDING_STATUS, REVIEWER_ID, clean_text, locked_jsonl_paths, read_jsonl, sanitize_url
from .youtube_video import is_youtube_url, sanitize_content_analysis, sanitize_media

Decision = Literal["approve", "reject", "hold"]
_APPROVED_STATUS = "approved_for_manual_links"
_APPROVED_BY_TAG = "approved-by-jiphyeonjeon-claw"
_VALID_DECISIONS: set[str] = {"approve", "reject", "hold"}
_METADATA_MAX_CHARS = 500_000
_METADATA_TIMEOUT_SEC = 5.0


@dataclass(frozen=True)
class PageMetadata:
    title: str = ""
    summary: str = ""
    published_at: str = ""


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
    enrich: bool = False,
    metadata_fetcher: Callable[[str], PageMetadata | None] | None = None,
    metadata_timeout_sec: float = _METADATA_TIMEOUT_SEC,
) -> list[dict[str, Any]]:
    with locked_jsonl_paths(queue_path, decisions_path):
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
    if enrich:
        approved = _enrich_manual_link_rows(approved, metadata_fetcher=metadata_fetcher, timeout_sec=metadata_timeout_sec)
    with locked_jsonl_paths(output_path):
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
    row = {
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
    media = sanitize_media(record.get("media"))
    if media:
        row["media"] = media
    content_analysis = sanitize_content_analysis(record.get("content_analysis"))
    if content_analysis:
        row["content_analysis"] = content_analysis
    return row



def _enrich_manual_link_rows(
    rows: list[dict[str, Any]],
    *,
    metadata_fetcher: Callable[[str], PageMetadata | None] | None,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    fetcher = metadata_fetcher or (lambda url: _fetch_page_metadata(url, timeout_sec=timeout_sec))
    enriched: list[dict[str, Any]] = []
    for row in rows:
        url = str(row.get("url") or "")
        if is_youtube_url(url):
            enriched.append(row)
            continue
        try:
            metadata = fetcher(url) if url else None
        except Exception:
            metadata = None
        enriched.append(_apply_page_metadata(row, metadata) if metadata else row)
    return enriched


def _apply_page_metadata(row: dict[str, Any], metadata: PageMetadata) -> dict[str, Any]:
    out = dict(row)
    title = clean_text(metadata.title, limit=180)
    summary = clean_text(metadata.summary, limit=700)
    published_at = _published_date(metadata.published_at)
    if title and _should_replace_title(str(out.get("title") or ""), str(out.get("url") or "")):
        out["title"] = title
    if summary and not clean_text(out.get("summary"), limit=700):
        out["summary"] = summary
    if published_at:
        out["published_at"] = published_at
    if title or summary or published_at:
        out["enrichment"] = {
            "source": "public_html_metadata",
            "title_applied": bool(title and out.get("title") == title),
            "summary_applied": bool(summary and out.get("summary") == summary),
            "published_at_applied": bool(published_at and out.get("published_at") == published_at),
        }
    return out


def _should_replace_title(title: str, url: str) -> bool:
    current = clean_text(title, limit=500).lower()
    if not current:
        return True
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    host_without_www = host.removeprefix("www.")
    return (
        current == host
        or current == host_without_www
        or current.startswith(f"{host}/")
        or current.startswith(f"{host_without_www}/")
    )


def _published_date(value: str) -> str:
    text = clean_text(value, limit=80)
    if len(text) >= 10 and text[:4].isdigit() and text[4] == "-" and text[7] == "-":
        return text[:10]
    return ""


def _fetch_page_metadata(url: str, *, timeout_sec: float) -> PageMetadata | None:
    try:
        import httpx

        safe_url = sanitize_url(url)
        if not safe_url:
            return None
        with httpx.stream(
            "GET",
            safe_url,
            follow_redirects=True,
            timeout=max(0.1, timeout_sec),
            headers={"User-Agent": "jiphyeonjeon-miner-review/0.1"},
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type:
                return None
            chunks: list[bytes] = []
            remaining = _METADATA_MAX_CHARS
            for chunk in response.iter_bytes():
                if not chunk or remaining <= 0:
                    break
                chunks.append(chunk[:remaining])
                remaining -= len(chunks[-1])
            encoding = response.encoding or "utf-8"
        html = b"".join(chunks).decode(encoding, errors="replace")
        return extract_page_metadata(html)
    except Exception:
        return None


def extract_page_metadata(html: str) -> PageMetadata:
    parser = _PageMetadataParser()
    parser.feed(html[:_METADATA_MAX_CHARS])
    return parser.metadata()


class _PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture_title = False
        self._title_parts: list[str] = []
        self._meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._capture_title = True
            return
        if tag.lower() != "meta":
            return
        values = {name.lower(): value or "" for name, value in attrs}
        key = (values.get("property") or values.get("name") or "").lower()
        content = clean_text(values.get("content"), limit=1000)
        if key and content and key not in self._meta:
            self._meta[key] = content

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._capture_title = False

    def metadata(self) -> PageMetadata:
        title = (
            self._meta.get("og:title")
            or self._meta.get("twitter:title")
            or clean_text(" ".join(self._title_parts), limit=500)
        )
        summary = (
            self._meta.get("og:description")
            or self._meta.get("twitter:description")
            or self._meta.get("description")
        )
        published_at = self._meta.get("article:published_time") or self._meta.get("date") or ""
        return PageMetadata(title=title, summary=summary, published_at=published_at)


def _append_jsonl_unlocked(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())
