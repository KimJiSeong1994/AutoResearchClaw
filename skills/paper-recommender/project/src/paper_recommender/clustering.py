"""Pure-Python embedding-based clustering for the daily-research pipeline.

Pipeline: candidate items → embed via OpenClaw ``/v1/embeddings`` →
k-means++ → list of Cluster (with centroid, top keywords, items).

Stays within the project's hard constraint of httpx + pyyaml only — no
numpy/scipy/sklearn. At ~300–500 items the pure-Python path runs in
single-digit seconds on Python 3.11+.

When embedding fails (network, model down, batch size limit hit), the
pipeline returns ``ClusterResult.used_fallback=True`` with a single bucket
containing all items, so the caller can bypass cluster_select and feed the
items into the legacy flat-rerank path instead of producing zero output.
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

from paper_recommender.config import ClusterSettings
from paper_recommender.sources import CandidateItem

log = logging.getLogger(__name__)

EmbeddingFn = Callable[[list[str]], Awaitable[list[list[float]]]]


# ─────────────────────────── Output dataclasses ───────────────────────────


@dataclass
class Cluster:
    id: int
    items: list[CandidateItem]
    centroid: list[float]
    centroid_keywords: list[str] = field(default_factory=list)
    coherence: float = 0.0  # mean cosine of members to centroid
    label: str = ""
    summary: str = ""

    @property
    def size(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class ClusterResult:
    clusters: list[Cluster]
    coherence: float  # mean coherence across clusters
    used_fallback: bool


# ─────────────────────────── Embedding client ───────────────────────────


class EmbeddingClient:
    """Thin OpenAI-compatible POST /embeddings client.

    The caller passes a fully-formed URL (e.g. ``http://host:18789/v1/embeddings``)
    rather than a base+endpoint pair, so this class makes no assumption about
    where ``/v1`` lives.
    """

    def __init__(
        self,
        url: str,
        token: str,
        model: str,
        *,
        timeout_sec: float = 120.0,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not url:
            raise ValueError("url is required")
        if not model:
            raise ValueError("model is required")
        self._url = url
        self._token = token
        self._model = model
        self._timeout_sec = timeout_sec
        self._transport = _transport

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client_kwargs: dict = {"timeout": self._timeout_sec}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        body = {"model": self._model, "input": texts}
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.post(self._url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        rows = data.get("data", [])
        if not isinstance(rows, list):
            raise RuntimeError("embedding response missing 'data' list")
        # Sort by ``index`` to be defensive — OpenAI-compat guarantees order
        # but proxies have been known to scramble it.
        rows = sorted(rows, key=lambda r: r.get("index", 0))
        return [list(r["embedding"]) for r in rows]


# ─────────────────────────── Vector ops ───────────────────────────


def _cosine_sim(a: list[float], b: list[float]) -> float:
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _mean_vector(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    n = len(vectors)
    d = len(vectors[0])
    out = [0.0] * d
    for v in vectors:
        for i in range(d):
            out[i] += v[i]
    return [x / n for x in out]


# ─────────────────────────── K-means++ ───────────────────────────


def _kmeans(
    vectors: list[list[float]],
    k: int,
    *,
    max_iter: int = 20,
    seed: int = 0,
) -> tuple[list[list[int]], list[list[float]]]:
    """Return (clusters_of_indices, centroids). k-means++ init, cosine distance."""

    n = len(vectors)
    if n == 0 or k <= 0:
        return [], []
    k = min(k, n)
    rng = random.Random(seed)

    # k-means++ initialization
    first_idx = rng.randrange(n)
    centroids: list[list[float]] = [list(vectors[first_idx])]
    for _ in range(k - 1):
        dists2: list[float] = []
        for v in vectors:
            min_d = min(1.0 - _cosine_sim(v, c) for c in centroids)
            min_d = max(min_d, 0.0)
            dists2.append(min_d * min_d)
        total = sum(dists2)
        if total <= 0.0:
            centroids.append(list(rng.choice(vectors)))
            continue
        target = rng.uniform(0.0, total)
        accum = 0.0
        picked = 0
        for i, d in enumerate(dists2):
            accum += d
            if accum >= target:
                picked = i
                break
        centroids.append(list(vectors[picked]))

    assignments: list[int] = [-1] * n
    for _ in range(max_iter):
        new_assign: list[int] = [0] * n
        for i, v in enumerate(vectors):
            best_c = 0
            best_sim = -2.0
            for ci, c in enumerate(centroids):
                s = _cosine_sim(v, c)
                if s > best_sim:
                    best_sim = s
                    best_c = ci
            new_assign[i] = best_c
        if new_assign == assignments:
            break
        assignments = new_assign
        for ci in range(k):
            members = [vectors[i] for i in range(n) if assignments[i] == ci]
            if members:
                centroids[ci] = _mean_vector(members)

    clusters: list[list[int]] = [[] for _ in range(k)]
    for i, ci in enumerate(assignments):
        clusters[ci].append(i)
    return clusters, centroids


# ─────────────────────────── Keywords ───────────────────────────


_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "for", "on", "with",
    "is", "are", "be", "this", "that", "by", "from", "as", "at", "it",
    "we", "our", "i", "you", "they", "their", "via", "using",
    "based", "novel", "new", "approach", "method", "methods", "model",
    "models", "paper", "papers", "study", "studies", "result", "results",
    "show", "shows", "shown", "propose", "proposed", "proposes",
    "framework", "system", "systems", "use", "used", "uses", "case",
    "task", "tasks", "data", "set", "sets", "into", "out", "than", "more",
    "less", "very", "such", "also", "however", "moreover", "thus",
})

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]+")


def _top_keywords(items: list[CandidateItem], top_n: int = 5) -> list[str]:
    counter: Counter[str] = Counter()
    for item in items:
        for word in _WORD_RE.findall(item.title or ""):
            lw = word.lower()
            if lw in _STOPWORDS or len(lw) <= 2:
                continue
            counter[lw] += 1
    return [w for w, _ in counter.most_common(top_n)]


# ─────────────────────────── Top-level cluster() ───────────────────────────


def _build_text_for_embedding(item: CandidateItem) -> str:
    parts = [(item.title or "").strip()]
    if item.abstract:
        parts.append(item.abstract[:500].strip())
    return " — ".join(p for p in parts if p)


async def cluster_candidates(
    items: list[CandidateItem],
    *,
    embed_fn: EmbeddingFn,
    cluster_settings: ClusterSettings,
    embed_batch_size: int = 64,
    min_cluster_size: int = 2,
    seed: int = 0,
) -> ClusterResult:
    """Cluster ``items`` into up to ``cluster_settings.max_clusters`` groups.

    On any embedding failure or count mismatch, returns a single-cluster
    fallback with ``used_fallback=True`` so callers can route to flat rerank.
    """

    if not items:
        return ClusterResult(clusters=[], coherence=0.0, used_fallback=False)

    texts = [_build_text_for_embedding(item) for item in items]
    embeddings: list[list[float]] = []
    try:
        for start in range(0, len(texts), embed_batch_size):
            batch = texts[start : start + embed_batch_size]
            vecs = await embed_fn(batch)
            embeddings.extend(vecs)
    except Exception as e:
        log.warning("embedding failed; falling back to single bucket: %s", e)
        return _fallback_single(items)

    if len(embeddings) != len(items):
        log.warning(
            "embedding count mismatch (%d vs %d); falling back",
            len(embeddings),
            len(items),
        )
        return _fallback_single(items)

    k = max(1, min(cluster_settings.max_clusters, len(items)))
    indices_by_cluster, centroids = _kmeans(embeddings, k, seed=seed)

    out: list[Cluster] = []
    coherence_sum = 0.0
    coherence_count = 0
    for ci, idxs in enumerate(indices_by_cluster):
        if len(idxs) < min_cluster_size:
            continue
        cluster_items = [items[i] for i in idxs]
        cluster_vecs = [embeddings[i] for i in idxs]
        sims = [_cosine_sim(v, centroids[ci]) for v in cluster_vecs]
        coh = sum(sims) / len(sims) if sims else 0.0
        coherence_sum += coh
        coherence_count += 1

        out.append(
            Cluster(
                id=len(out),
                items=cluster_items,
                centroid=list(centroids[ci]),
                centroid_keywords=_top_keywords(cluster_items),
                coherence=coh,
            )
        )

    overall = coherence_sum / coherence_count if coherence_count else 0.0
    return ClusterResult(clusters=out, coherence=overall, used_fallback=False)


def _fallback_single(items: list[CandidateItem]) -> ClusterResult:
    return ClusterResult(
        clusters=[
            Cluster(
                id=0,
                items=list(items),
                centroid=[],
                centroid_keywords=_top_keywords(items),
                coherence=0.0,
            )
        ],
        coherence=0.0,
        used_fallback=True,
    )


__all__ = [
    "Cluster",
    "ClusterResult",
    "EmbeddingClient",
    "EmbeddingFn",
    "cluster_candidates",
]
