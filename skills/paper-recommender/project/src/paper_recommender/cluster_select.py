"""LLM-driven cluster ranking + labelling.

Given a list of :class:`Cluster` objects (typically 5–10 from the k-means
step) and a reader profile (SOUL markdown), pick the top
``max_clusters`` and write a 5–8 word label + 1–2 sentence Korean summary
onto each picked cluster.

The LLM call uses a JSON-strict listwise prompt — same family as
``rerank.py`` but at cluster granularity (1 line per cluster, not per
paper). On any LLM failure or malformed JSON we fall back to a
deterministic size-based pick so the pipeline never silently produces
empty output.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from paper_recommender.clustering import Cluster

log = logging.getLogger(__name__)

ChatJsonFn = Callable[[list[dict[str, str]]], Awaitable[dict[str, Any]]]


_SYSTEM_TEMPLATE = """You are a research librarian. Rank ALL clusters from \
most to least important for the reader described in <reader_profile>. \
For the top {max_clusters} clusters, generate a 5–8 word descriptive \
label and a 1–2 sentence Korean summary explaining why this cluster \
matters to this reader.

Respond strictly as JSON:
{{"ranking": [{{"id": int, "rank": int, "label": str, "summary": str}}, ...]}}

Rules:
- Every input cluster MUST appear exactly once in ranking with rank \
1..N (1 = most important).
- Include label and summary ONLY for clusters with rank <= {max_clusters}.
- For clusters beyond rank {max_clusters}, set label and summary to "".
- Korean summaries; English labels OK if the topic is technical."""


def _safe(s: str | None, limit: int = 200) -> str:
    """Strip XML-injection-prone characters and cap length."""
    s = (s or "").replace("<", "(").replace(">", ")").replace("\n", " ")
    return s[:limit].strip()


def _cluster_line(c: Cluster) -> str:
    samples = " / ".join(_safe(it.title, 80) for it in c.items[:3])
    keywords = ", ".join(c.centroid_keywords[:5]) or "(none)"
    return (
        f"[{c.id}] keywords: {keywords} "
        f"| size: {len(c.items)} "
        f"| samples: {samples}"
    )


async def select_top_clusters(
    clusters: list[Cluster],
    *,
    chat_json: ChatJsonFn,
    max_clusters: int,
    soul_md: str | None = None,
) -> list[Cluster]:
    """Rank, pick top-N, and decorate clusters with label + summary.

    On LLM failure: deterministic size-fallback (largest clusters first,
    auto-labelled from centroid keywords).
    """

    if not clusters:
        return []
    if max_clusters <= 0:
        return []

    profile_text = (soul_md or "").strip()[:3500] or "(no reader profile available)"
    profile_block = f"<reader_profile>\n{profile_text}\n</reader_profile>"
    cluster_block = (
        "<clusters>\n" + "\n".join(_cluster_line(c) for c in clusters) + "\n</clusters>"
    )
    user_msg = (
        f"{profile_block}\n\n{cluster_block}\n\n"
        f"Rank all {len(clusters)} clusters; "
        f"label and summarize the top {max_clusters}."
    )
    system_msg = _SYSTEM_TEMPLATE.format(max_clusters=max_clusters)

    try:
        result = await chat_json(
            [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ]
        )
    except Exception as e:
        log.warning("cluster_select LLM failed (%s); size-fallback", e)
        return _fallback_top_by_size(clusters, max_clusters)

    return _apply_ranking(clusters, result, max_clusters)


def _fallback_top_by_size(clusters: list[Cluster], max_clusters: int) -> list[Cluster]:
    sorted_c = sorted(clusters, key=lambda c: -len(c.items))
    picked = sorted_c[:max_clusters]
    for c in picked:
        if not c.label:
            c.label = ", ".join(c.centroid_keywords[:3]) or f"Cluster {c.id}"
        if not c.summary:
            kws = ", ".join(c.centroid_keywords[:5])
            c.summary = f"{len(c.items)}개 항목으로 구성된 클러스터. 주요 키워드: {kws}"
    return picked


def _apply_ranking(
    clusters: list[Cluster],
    llm_result: Any,
    max_clusters: int,
) -> list[Cluster]:
    if not isinstance(llm_result, dict):
        log.warning("cluster_select: LLM returned non-dict; size-fallback")
        return _fallback_top_by_size(clusters, max_clusters)

    ranking = llm_result.get("ranking")
    if not isinstance(ranking, list) or not ranking:
        log.warning("cluster_select: LLM returned no ranking; size-fallback")
        return _fallback_top_by_size(clusters, max_clusters)

    by_id = {c.id: c for c in clusters}
    decorated: list[tuple[int, Cluster, str, str]] = []
    for entry in ranking:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        rank = entry.get("rank")
        if not isinstance(cid, int) or cid not in by_id:
            continue
        if not isinstance(rank, int) or rank < 1:
            continue
        label = str(entry.get("label", "") or "").strip()
        summary = str(entry.get("summary", "") or "").strip()
        decorated.append((rank, by_id[cid], label, summary))

    if not decorated:
        return _fallback_top_by_size(clusters, max_clusters)

    decorated.sort(key=lambda x: x[0])
    picked: list[Cluster] = []
    seen_ids: set[int] = set()
    for rank, c, label, summary in decorated:
        if c.id in seen_ids:
            continue
        if len(picked) >= max_clusters:
            break
        seen_ids.add(c.id)
        c.label = label or (", ".join(c.centroid_keywords[:3]) or f"Cluster {c.id}")
        c.summary = summary
        picked.append(c)

    if not picked:
        return _fallback_top_by_size(clusters, max_clusters)
    return picked


__all__ = ["ChatJsonFn", "select_top_clusters"]
