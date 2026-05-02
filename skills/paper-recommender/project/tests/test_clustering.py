from __future__ import annotations

import asyncio

import httpx

from paper_recommender.clustering import (
    Cluster,
    ClusterResult,
    EmbeddingClient,
    cluster_candidates,
    _cosine_sim,
    _kmeans,
    _top_keywords,
)
from paper_recommender.config import ClusterSettings
from paper_recommender.sources import CandidateItem


# ─────────────── helpers ───────────────


def _items(*titles: str) -> list[CandidateItem]:
    return [CandidateItem(source="t", title=t) for t in titles]


def _stub_embed(by_text: dict[str, list[float]]):
    async def fn(texts: list[str]) -> list[list[float]]:
        return [by_text[t] for t in texts]

    return fn


def _settings(max_clusters: int = 3) -> ClusterSettings:
    return ClusterSettings(max_clusters=max_clusters)


# ─────────────── vector ops ───────────────


def test_cosine_sim_identical_vectors_is_one() -> None:
    assert _cosine_sim([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_sim_orthogonal_is_zero() -> None:
    assert _cosine_sim([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_sim_zero_vectors() -> None:
    assert _cosine_sim([0.0, 0.0], [1.0, 1.0]) == 0.0


# ─────────────── kmeans ───────────────


def test_kmeans_groups_two_obvious_clusters() -> None:
    # Two tight clusters in 2-D
    vecs = [
        [1.0, 0.0], [1.0, 0.01], [1.0, -0.01],   # cluster A
        [0.0, 1.0], [0.01, 1.0], [-0.01, 1.0],   # cluster B
    ]
    clusters, _ = _kmeans(vecs, k=2, seed=42)
    assert len(clusters) == 2
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [3, 3]


def test_kmeans_handles_k_greater_than_n() -> None:
    vecs = [[1.0, 0.0], [0.0, 1.0]]
    clusters, _ = _kmeans(vecs, k=10, seed=0)
    # Effective k clamped to n; total membership equals n
    total = sum(len(c) for c in clusters)
    assert total == 2


def test_kmeans_empty_returns_empty() -> None:
    clusters, centroids = _kmeans([], k=3)
    assert clusters == [] and centroids == []


# ─────────────── keywords ───────────────


def test_top_keywords_filters_stopwords_and_short_words() -> None:
    items = _items(
        "A novel transformer attention mechanism for the new task",
        "Transformer training methods on the data",
        "Attention is all you need transformer paper",
    )
    kws = _top_keywords(items, top_n=3)
    # Stopwords (the, is, a, all, you, new, paper, methods, task, data) gone.
    # "transformer" and "attention" should win on frequency.
    assert "transformer" in kws
    assert "attention" in kws
    assert "the" not in kws
    assert "is" not in kws


def test_top_keywords_handles_empty_titles() -> None:
    assert _top_keywords([CandidateItem(source="x", title="")], 5) == []


# ─────────────── cluster_candidates: full pipeline ───────────────


def test_cluster_candidates_groups_by_embedding() -> None:
    titles_a = ["transformer attention", "transformer training"]
    titles_b = ["graph embedding methods", "graph neural networks"]
    titles = titles_a + titles_b
    items = _items(*titles)
    # Inputs to embed_fn are built from item; need to reconstruct the same
    # shape that cluster_candidates uses for embed text.
    from paper_recommender.clustering import _build_text_for_embedding
    texts = [_build_text_for_embedding(it) for it in items]

    by_text = {
        texts[0]: [1.0, 0.0],
        texts[1]: [1.0, 0.01],
        texts[2]: [0.0, 1.0],
        texts[3]: [0.01, 1.0],
    }
    result = asyncio.run(
        cluster_candidates(
            items,
            embed_fn=_stub_embed(by_text),
            cluster_settings=_settings(max_clusters=2),
        )
    )
    assert isinstance(result, ClusterResult)
    assert not result.used_fallback
    assert len(result.clusters) == 2
    # Each cluster has 2 items (perfect split)
    sizes = sorted(c.size for c in result.clusters)
    assert sizes == [2, 2]


def test_cluster_candidates_falls_back_when_embedding_raises() -> None:
    items = _items("a", "b", "c")

    async def boom(_texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding service down")

    result = asyncio.run(
        cluster_candidates(
            items,
            embed_fn=boom,
            cluster_settings=_settings(),
        )
    )
    assert result.used_fallback
    assert len(result.clusters) == 1
    assert result.clusters[0].size == 3


def test_cluster_candidates_falls_back_on_count_mismatch() -> None:
    items = _items("a", "b", "c")

    async def short(_texts: list[str]) -> list[list[float]]:
        # Returns fewer vectors than inputs
        return [[1.0, 0.0]]

    result = asyncio.run(
        cluster_candidates(
            items,
            embed_fn=short,
            cluster_settings=_settings(),
        )
    )
    assert result.used_fallback


def test_cluster_candidates_min_cluster_size_drops_singletons() -> None:
    items = _items("a", "b", "c")
    from paper_recommender.clustering import _build_text_for_embedding
    texts = [_build_text_for_embedding(it) for it in items]
    # Force one item into its own cluster by giving it a wildly different vector
    by_text = {
        texts[0]: [1.0, 0.0],
        texts[1]: [1.0, 0.01],
        texts[2]: [0.0, -1.0],
    }
    result = asyncio.run(
        cluster_candidates(
            items,
            embed_fn=_stub_embed(by_text),
            cluster_settings=_settings(max_clusters=2),
            min_cluster_size=2,
        )
    )
    # The singleton cluster should be dropped; only the 2-item cluster remains.
    assert all(c.size >= 2 for c in result.clusters)


def test_cluster_candidates_empty_input_returns_empty() -> None:
    result = asyncio.run(
        cluster_candidates(
            [],
            embed_fn=_stub_embed({}),
            cluster_settings=_settings(),
        )
    )
    assert result.clusters == []
    assert not result.used_fallback


def test_cluster_candidates_batches_embedding_calls() -> None:
    items = _items(*[f"title {i}" for i in range(5)])
    from paper_recommender.clustering import _build_text_for_embedding

    seen_batch_sizes: list[int] = []

    async def fn(texts: list[str]) -> list[list[float]]:
        seen_batch_sizes.append(len(texts))
        return [[1.0, float(i)] for i, _ in enumerate(texts)]

    asyncio.run(
        cluster_candidates(
            items,
            embed_fn=fn,
            cluster_settings=_settings(),
            embed_batch_size=2,
        )
    )
    # 5 items, batch size 2 → batches of 2, 2, 1
    assert seen_batch_sizes == [2, 2, 1]


# ─────────────── EmbeddingClient (HTTP) ───────────────


def test_embedding_client_posts_and_extracts_embeddings() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(request.url)
        captured["body"] = _json.loads(request.content.decode())
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
                    {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
                ],
                "model": "openclaw/clawbridge",
            },
        )

    client = EmbeddingClient(
        url="http://test.example/v1/embeddings",
        token="TOK",
        model="openclaw/clawbridge",
        _transport=httpx.MockTransport(handler),
    )
    out = asyncio.run(client.embed_batch(["hello", "world"]))
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    assert captured["url"] == "http://test.example/v1/embeddings"
    assert captured["auth"] == "Bearer TOK"
    assert captured["body"]["model"] == "openclaw/clawbridge"
    assert captured["body"]["input"] == ["hello", "world"]


def test_embedding_client_sorts_by_index_defensively() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [
                {"index": 1, "embedding": [9.0]},
                {"index": 0, "embedding": [1.0]},
            ]},
        )

    client = EmbeddingClient(
        url="http://t.example/embeddings",
        token="x",
        model="m",
        _transport=httpx.MockTransport(handler),
    )
    out = asyncio.run(client.embed_batch(["a", "b"]))
    assert out == [[1.0], [9.0]]


def test_embedding_client_raises_on_5xx() -> None:
    import pytest

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="gateway down")

    client = EmbeddingClient(
        url="http://t.example/embeddings",
        token="x",
        model="m",
        _transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.embed_batch(["x"]))


def test_embedding_client_empty_input_returns_empty_without_call() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    client = EmbeddingClient(
        url="http://t.example/embeddings",
        token="x",
        model="m",
        _transport=httpx.MockTransport(handler),
    )
    out = asyncio.run(client.embed_batch([]))
    assert out == []


def test_embedding_client_validates_inputs() -> None:
    import pytest

    with pytest.raises(ValueError):
        EmbeddingClient(url="", token="x", model="m")
    with pytest.raises(ValueError):
        EmbeddingClient(url="http://x", token="x", model="")
