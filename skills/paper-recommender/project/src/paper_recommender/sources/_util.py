"""Shared utilities for source adapters."""

from __future__ import annotations

import re

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


__all__ = ["normalize_title_for_dedup"]
