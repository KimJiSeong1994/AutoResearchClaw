from __future__ import annotations

import asyncio

from paper_recommender.cluster_select import select_top_clusters
from paper_recommender.clustering import Cluster
from paper_recommender.sources import CandidateItem


def _cluster(cid: int, n: int, *, kw: list[str] | None = None) -> Cluster:
    return Cluster(
        id=cid,
        items=[CandidateItem(source="x", title=f"item {cid}-{i}") for i in range(n)],
        centroid=[],
        centroid_keywords=kw or [f"kw{cid}"],
    )


def _stub_chat_json(response: dict | Exception):
    async def fn(_messages):
        if isinstance(response, Exception):
            raise response
        return response

    return fn


def test_select_returns_top_n_with_labels() -> None:
    clusters = [_cluster(0, 5), _cluster(1, 3), _cluster(2, 7)]
    response = {
        "ranking": [
            {"id": 2, "rank": 1, "label": "Best topic", "summary": "왜 중요한가"},
            {"id": 0, "rank": 2, "label": "Second", "summary": "두번째"},
            {"id": 1, "rank": 3, "label": "", "summary": ""},
        ]
    }
    out = asyncio.run(
        select_top_clusters(
            clusters,
            chat_json=_stub_chat_json(response),
            max_clusters=2,
        )
    )
    assert [c.id for c in out] == [2, 0]
    assert out[0].label == "Best topic"
    assert out[0].summary == "왜 중요한가"
    assert out[1].label == "Second"


def test_select_size_fallback_on_llm_exception() -> None:
    clusters = [_cluster(0, 3), _cluster(1, 7), _cluster(2, 5)]
    out = asyncio.run(
        select_top_clusters(
            clusters,
            chat_json=_stub_chat_json(RuntimeError("LLM died")),
            max_clusters=2,
        )
    )
    # Largest first: cluster 1 (7), cluster 2 (5)
    assert [c.id for c in out] == [1, 2]
    # Fallback labels are non-empty
    assert all(c.label for c in out)
    assert all(c.summary for c in out)


def test_select_size_fallback_on_malformed_response() -> None:
    clusters = [_cluster(0, 5), _cluster(1, 3)]
    out = asyncio.run(
        select_top_clusters(
            clusters,
            chat_json=_stub_chat_json({"not_ranking": []}),
            max_clusters=1,
        )
    )
    assert len(out) == 1
    assert out[0].id == 0  # the larger cluster


def test_select_size_fallback_on_non_dict_response() -> None:
    clusters = [_cluster(0, 5)]
    out = asyncio.run(
        select_top_clusters(
            clusters,
            chat_json=_stub_chat_json("not a dict"),  # type: ignore[arg-type]
            max_clusters=1,
        )
    )
    assert len(out) == 1


def test_select_skips_unknown_cluster_ids_in_ranking() -> None:
    clusters = [_cluster(0, 5), _cluster(1, 3)]
    response = {
        "ranking": [
            {"id": 99, "rank": 1, "label": "ghost", "summary": "x"},
            {"id": 1, "rank": 2, "label": "real", "summary": "y"},
        ]
    }
    out = asyncio.run(
        select_top_clusters(clusters, chat_json=_stub_chat_json(response), max_clusters=1),
    )
    assert [c.id for c in out] == [1]


def test_select_dedupes_repeated_cluster_ids() -> None:
    clusters = [_cluster(0, 5), _cluster(1, 3)]
    response = {
        "ranking": [
            {"id": 0, "rank": 1, "label": "first", "summary": ""},
            {"id": 0, "rank": 2, "label": "first-again", "summary": ""},
            {"id": 1, "rank": 3, "label": "second", "summary": ""},
        ]
    }
    out = asyncio.run(
        select_top_clusters(clusters, chat_json=_stub_chat_json(response), max_clusters=2),
    )
    assert [c.id for c in out] == [0, 1]
    assert out[0].label == "first"


def test_select_empty_clusters_returns_empty() -> None:
    out = asyncio.run(
        select_top_clusters([], chat_json=_stub_chat_json({}), max_clusters=3),
    )
    assert out == []


def test_select_zero_max_returns_empty() -> None:
    clusters = [_cluster(0, 5)]
    out = asyncio.run(
        select_top_clusters(clusters, chat_json=_stub_chat_json({}), max_clusters=0),
    )
    assert out == []


def test_select_passes_soul_in_user_message() -> None:
    captured: dict = {}

    async def fn(messages):
        captured["messages"] = messages
        return {"ranking": [{"id": 0, "rank": 1, "label": "L", "summary": "S"}]}

    clusters = [_cluster(0, 3)]
    asyncio.run(
        select_top_clusters(
            clusters,
            chat_json=fn,
            max_clusters=1,
            soul_md="# My SOUL\n- I love graphs",
        )
    )
    user_msg = captured["messages"][1]["content"]
    assert "<reader_profile>" in user_msg
    assert "I love graphs" in user_msg
    assert "<clusters>" in user_msg


def test_select_fallback_label_uses_keywords_when_llm_label_empty() -> None:
    clusters = [_cluster(0, 5, kw=["transformer", "attention"])]
    response = {
        "ranking": [{"id": 0, "rank": 1, "label": "", "summary": "explained"}]
    }
    out = asyncio.run(
        select_top_clusters(clusters, chat_json=_stub_chat_json(response), max_clusters=1),
    )
    assert "transformer" in out[0].label or "attention" in out[0].label
    assert out[0].summary == "explained"
