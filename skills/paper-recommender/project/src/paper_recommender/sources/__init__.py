"""Source adapter framework for the multi-source daily-research pipeline.

Each adapter (arxiv, papers_with_code, semantic_scholar, hackernews,
github_trending, rss, jiphyeonjeon) implements :class:`SourceAdapter` and
returns :class:`CandidateItem` instances. :func:`fetch_all_sources` fans out
to all adapters concurrently with per-adapter failure isolation.

Concrete adapters live in sibling modules (Phase B). This module is the
contract.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import ClassVar, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Per-adapter wall-clock grace beyond limits.timeout_sec. Adapters are expected
# to honor limits.timeout_sec for their internal HTTP calls; the grace covers
# httpx connection-close races and gather scheduling overhead.
_FETCH_TIMEOUT_GRACE_SEC = 5.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CandidateItem:
    """One candidate piece of content from a single source.

    Frozen + hashable so downstream dedup can use sets. Use tuples (not lists)
    for any list-like fields; they are immutable and hashable.

    Long abstracts (>``MAX_ABSTRACT_CHARS``) are auto-truncated at construction
    so a runaway RSS feed with a full blog body cannot blow the embedding
    token budget downstream.
    """

    MAX_ABSTRACT_CHARS: ClassVar[int] = 1500

    source: str
    title: str
    url: str | None = None
    abstract: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    arxiv_id: str | None = None
    doi: str | None = None
    tags: tuple[str, ...] = ()
    score: float | None = None
    fetched_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        if self.abstract is not None and len(self.abstract) > self.MAX_ABSTRACT_CHARS:
            cut = self.abstract[: self.MAX_ABSTRACT_CHARS].rstrip()
            object.__setattr__(self, "abstract", cut + " [...]")


@dataclass(frozen=True)
class SourceLimits:
    """Per-fetch knobs passed to every adapter."""

    max_per_source: int = 50
    year_from: int | None = None
    timeout_sec: float = 30.0


@runtime_checkable
class SourceAdapter(Protocol):
    """Anything that can turn seed topics into :class:`CandidateItem` lists."""

    name: str

    async def fetch(
        self,
        seed_topics: list[str],
        limits: SourceLimits,
    ) -> list[CandidateItem]: ...


async def fetch_all_sources(
    adapters: list[SourceAdapter],
    seed_topics: list[str],
    limits: SourceLimits,
) -> dict[str, list[CandidateItem]]:
    """Fan out to all adapters concurrently.

    Per-adapter failure is logged at WARNING and that source is omitted from
    the returned mapping. Other adapters are unaffected. The mapping key is
    ``adapter.name`` and is preserved insertion-order from ``adapters``.
    """

    if not adapters:
        return {}

    async def _wrap(adapter: SourceAdapter) -> tuple[str, list[CandidateItem]]:
        items = await asyncio.wait_for(
            adapter.fetch(seed_topics, limits),
            timeout=limits.timeout_sec + _FETCH_TIMEOUT_GRACE_SEC,
        )
        return adapter.name, items

    results = await asyncio.gather(
        *(_wrap(a) for a in adapters),
        return_exceptions=True,
    )

    out: dict[str, list[CandidateItem]] = {}
    for adapter, result in zip(adapters, results):
        if isinstance(result, BaseException):
            logger.warning(
                "source adapter %r failed: %s: %s",
                adapter.name,
                type(result).__name__,
                result,
            )
            continue
        name, items = result
        out[name] = list(items)
    return out


__all__ = [
    "CandidateItem",
    "SourceAdapter",
    "SourceLimits",
    "fetch_all_sources",
]
