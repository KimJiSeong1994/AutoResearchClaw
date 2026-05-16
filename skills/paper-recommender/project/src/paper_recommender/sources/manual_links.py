"""Operator-supplied link source adapter.

Designed for compliance-sensitive sources such as LinkedIn. The adapter never
logs in, crawls, follows profile pages, or scrapes platform content. It only
reads local JSONL files explicitly provided by the operator and emits their
metadata into the candidate pipeline.
"""

from __future__ import annotations

import ipaddress
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from paper_recommender.sources import CandidateItem, SourceLimits
from paper_recommender.sources._util import (
    clean_text,
    item_matches_topics,
    normalize_title_for_dedup,
    redacted_path,
)

log = logging.getLogger(__name__)

PENDING_REVIEW_STATUSES = {"pending_claw_review", "pending_source_review", "pending"}
REVIEW_GATED_SOURCES = {"discord_miner", "discord_traveler"}


@dataclass(frozen=True)
class ManualLinkSettings:
    """Local JSONL files of user-provided links.

    Each line may contain ``title``, ``url``, ``summary``/``abstract``,
    ``author``/``authors``, ``published_at``/``date``, ``source`` and ``tags``.
    This is the safe LinkedIn path: users provide specific URLs/metadata; the
    pipeline does not automate LinkedIn access.
    """

    paths: list[str] = field(default_factory=list)
    max_file_kb: int = 512
    max_summary_chars: int = 700


class ManualLinksAdapter:
    name = "manual_links"

    def __init__(self, settings: ManualLinkSettings) -> None:
        self._settings = settings

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]:
        topics = [t.lower().strip() for t in seed_topics if t.strip()]
        out: list[CandidateItem] = []
        seen: set[str] = set()
        for raw_path in self._settings.paths:
            if len(out) >= limits.max_per_source:
                break
            path = Path(raw_path).expanduser()
            safe_path = _validated_path(path, self._settings.max_file_kb)
            if safe_path is None:
                continue
            try:
                lines = safe_path.read_text(encoding="utf-8").splitlines()
            except OSError as e:
                log.warning("manual_links cannot read %s: %s", redacted_path(path), e)
                continue
            for line_no, line in enumerate(lines, start=1):
                if len(out) >= limits.max_per_source:
                    break
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as e:
                    log.warning("manual_links invalid JSONL %s:%d: %s", redacted_path(path), line_no, e)
                    continue
                item = _to_item(raw, self._settings.max_summary_chars)
                if item is None:
                    continue
                if limits.year_from and item.year and item.year < limits.year_from:
                    continue
                if topics and not item_matches_topics(item, topics, include_tags=True):
                    continue
                key = item.url or normalize_title_for_dedup(item.title)
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(item)
        return out


def _validated_path(path: Path, max_file_kb: int) -> Path | None:
    display = redacted_path(path)
    if path.is_symlink():
        log.warning("manual_links symlink rejected: %s", display)
        return None
    if not path.is_file():
        log.warning("manual_links path missing or not a file: %s", display)
        return None
    try:
        real = path.resolve(strict=True)
        if real.stat().st_size > max_file_kb * 1024:
            log.warning("manual_links file exceeds max_file_kb: %s", display)
            return None
        return real
    except OSError as e:
        log.warning("manual_links cannot stat %s: %s", display, e)
        return None


def _to_item(raw: object, max_summary_chars: int) -> CandidateItem | None:
    if not isinstance(raw, dict):
        return None
    title = clean_text(raw.get("title"))
    url = clean_text(raw.get("url"))
    if not title or not _safe_http_url(url) or not _approved_miner_row(raw):
        return None
    summary = clean_text(raw.get("summary") or raw.get("abstract"))
    authors = _authors(raw)
    published = clean_text(raw.get("published_at") or raw.get("date"))
    source = _source(raw.get("source"), url)
    tags = tuple(dict.fromkeys(("manual-link", source, *_tags(raw))))
    return CandidateItem(
        source=source,
        title=title,
        url=url,
        abstract=summary[:max_summary_chars] if summary else None,
        authors=authors,
        year=_year_from_date(published),
        venue=_venue(source, url),
        tags=tags,
        score=1.0,
    )


def _safe_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password or not parsed.hostname:
        return False
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".localhost", ".internal", ".lan")):
        return False
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _approved_miner_row(raw: dict) -> bool:
    source = clean_text(raw.get("source")).lower()
    status = clean_text(raw.get("status")).lower()
    review = raw.get("review")
    if status in PENDING_REVIEW_STATUSES:
        return False
    if source not in REVIEW_GATED_SOURCES and not source.startswith("jiphyeonjeon"):
        return True
    if not isinstance(review, dict):
        return False
    decision = clean_text(review.get("decision")).lower()
    source_decision = clean_text(review.get("source_decision")).lower()
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    clean_tags = {clean_text(tag).lower() for tag in tags}
    return (
        decision == "approved"
        and source_decision in {"", "approve", "approved"}
        and "approved-by-jiphyeonjeon-claw" in clean_tags
    )


def _source(value: object, url: str) -> str:
    raw = clean_text(value).lower().replace(" ", "_")
    if raw:
        return raw[:40]
    host = urlparse(url).netloc.lower()
    if host.endswith("linkedin.com") or ".linkedin.com" in host:
        return "linkedin_manual"
    if host.endswith("medium.com") or ".medium.com" in host:
        return "medium_manual"
    return "manual_links"


def _venue(source: str, url: str) -> str:
    if source == "linkedin_manual":
        return "LinkedIn user-provided link"
    if source == "medium_manual":
        return "Medium user-provided link"
    return urlparse(url).netloc.lower() or "user-provided link"


def _authors(raw: dict) -> tuple[str, ...]:
    val = raw.get("authors")
    if isinstance(val, list):
        return tuple(clean_text(v) for v in val if clean_text(v))
    author = clean_text(raw.get("author"))
    return (author,) if author else ()


def _tags(raw: dict) -> tuple[str, ...]:
    val = raw.get("tags")
    if not isinstance(val, list):
        return ()
    return tuple(clean_text(v) for v in val if clean_text(v))


def _year_from_date(value: str) -> int | None:
    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).year
    except ValueError:
        return None


__all__ = ["ManualLinksAdapter", "ManualLinkSettings"]
