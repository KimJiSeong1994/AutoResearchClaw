"""Shared utilities for source adapters."""

from __future__ import annotations

import re
from pathlib import Path

from paper_recommender.sources import CandidateItem

# Common platform-prefix patterns for non-academic sources. Apply BEFORE
# punctuation-stripping so the colon at the end of "Show HN:" survives the
# match. Order matters only insofar as "show" appears in multiple variants.
_PLATFORM_PREFIX_RE = re.compile(
    r"^\s*(show hn:|ask hn:|tell hn:|launch hn:|\[d\]|\[r\]|\[p\]|\[n\]|"
    r"announce:|release:)\s*",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_title_for_dedup(title: str | None) -> str:
    """Reduce a title to a casing/whitespace/punctuation-invariant key.

    Use for non-academic sources (HN, GitHub trending, RSS) where the same
    content surfaces with cosmetic variations. Academic papers should rely
    on ``candidates.paper_key`` which uses structured IDs (paper_id,
    arxiv_id, doi).

    The transform: lowercase → strip a single platform prefix (Show HN:,
    [D], etc.) → drop punctuation → collapse whitespace.

    >>> normalize_title_for_dedup("Show HN:  My LLM Tool!")
    'my llm tool'
    >>> normalize_title_for_dedup("show hn: my llm tool")
    'my llm tool'
    """

    if not title:
        return ""
    s = title.lower().strip()
    s = _PLATFORM_PREFIX_RE.sub("", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    return s


def clean_text(value: object) -> str:
    """Collapse user/source text to one stripped line without changing content."""

    return " ".join(str(value or "").split()).strip()


def item_matches_topics(
    item: CandidateItem,
    topics: list[str],
    *,
    include_tags: bool = False,
) -> bool:
    """Return whether any normalized topic appears in source-visible item text."""

    fields = [item.title, item.abstract or "", item.venue or ""]
    if include_tags:
        fields.append(" ".join(item.tags))
    haystack = "\n".join(fields).lower()
    return any(topic in haystack for topic in topics)


def redacted_path(path: Path) -> str:
    """Log only a path basename so local source paths do not leak."""

    return f".../{path.name}" if path.name else "...(unnamed)"


__all__ = [
    "clean_text",
    "item_matches_topics",
    "normalize_title_for_dedup",
    "redacted_path",
]
