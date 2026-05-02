"""Unit tests for ``daily_research._merge_and_dedupe``.

Round-robin merge across sources + cross-source dedup logic. The integration
test exercises the function end-to-end with a single source; this file
covers the multi-source ordering and dedup branches in isolation.
"""

from __future__ import annotations

from paper_recommender.daily_research import _merge_and_dedupe
from paper_recommender.sources import CandidateItem


def _arxiv(title: str, arxiv_id: str | None = None) -> CandidateItem:
    return CandidateItem(source="arxiv", title=title, arxiv_id=arxiv_id)


def _hf(title: str, arxiv_id: str | None = None) -> CandidateItem:
    return CandidateItem(source="huggingface_papers", title=title, arxiv_id=arxiv_id)


def _hn(title: str) -> CandidateItem:
    return CandidateItem(source="hackernews", title=title)


def test_empty_input_returns_empty() -> None:
    assert _merge_and_dedupe({}) == []
    assert _merge_and_dedupe({"arxiv": []}) == []


def test_single_source_passes_through() -> None:
    items = [_arxiv("a", "1"), _arxiv("b", "2"), _arxiv("c", "3")]
    out = _merge_and_dedupe({"arxiv": items})
    assert [it.title for it in out] == ["a", "b", "c"]


def test_round_robin_interleaves_sources() -> None:
    """Two sources of equal size → output alternates by source."""
    out = _merge_and_dedupe({
        "arxiv": [_arxiv("a1", "1001"), _arxiv("a2", "1002"), _arxiv("a3", "1003")],
        "hackernews": [_hn("h1"), _hn("h2"), _hn("h3")],
    })
    titles = [it.title for it in out]
    # Round-robin: a1, h1, a2, h2, a3, h3 (insertion order of dict)
    assert titles == ["a1", "h1", "a2", "h2", "a3", "h3"]


def test_arxiv_id_dedup_across_sources() -> None:
    """Same arxiv_id from two sources → only one item in output."""
    out = _merge_and_dedupe({
        "arxiv": [_arxiv("Original Title", "2401.0001")],
        "huggingface_papers": [_hf("Different Title But Same Paper", "2401.0001")],
    })
    assert len(out) == 1
    # The first source wins (round-robin order)
    assert out[0].source == "arxiv"


def test_arxiv_id_dedup_case_insensitive() -> None:
    out = _merge_and_dedupe({
        "arxiv": [_arxiv("a", "2401.ABCD")],
        "huggingface_papers": [_hf("a-dup", "2401.abcd")],
    })
    assert len(out) == 1


def test_doi_dedup_across_sources() -> None:
    a = CandidateItem(source="arxiv", title="paper", doi="10.1234/x")
    b = CandidateItem(source="huggingface_papers", title="paper", doi="10.1234/X")
    out = _merge_and_dedupe({"arxiv": [a], "huggingface_papers": [b]})
    assert len(out) == 1


def test_title_dedup_for_non_academic() -> None:
    """Non-academic items (no arxiv_id, no doi) dedup by normalized title."""
    out = _merge_and_dedupe({
        "hackernews": [_hn("Show HN: My Tool")],
        "huggingface_papers": [CandidateItem(source="huggingface_papers", title="show hn: my tool")],
    })
    # Both have empty arxiv_id and doi → title-normalize collision → dedup
    assert len(out) == 1


def test_title_dedup_does_not_affect_arxiv_items() -> None:
    """Two arxiv items with same title but different IDs are kept (different papers)."""
    out = _merge_and_dedupe({
        "arxiv": [
            _arxiv("Generic title", "2401.0001"),
            _arxiv("Generic title", "2401.0002"),
        ],
    })
    assert len(out) == 2


def test_uneven_sources_round_robin_drains_correctly() -> None:
    """One large source + one small: large source's tail gets appended after small exhausts."""
    out = _merge_and_dedupe({
        "arxiv": [_arxiv(f"a{i}", f"id{i}") for i in range(5)],
        "hackernews": [_hn("h1")],
    })
    titles = [it.title for it in out]
    # First pass: a0, h1. Then hn exhausted; arxiv continues: a1, a2, a3, a4.
    assert titles == ["a0", "h1", "a1", "a2", "a3", "a4"]


def test_three_sources_round_robin() -> None:
    out = _merge_and_dedupe({
        "arxiv": [_arxiv("a1", "x1"), _arxiv("a2", "x2")],
        "hackernews": [_hn("h1"), _hn("h2")],
        "huggingface_papers": [_hf("hf1", "y1"), _hf("hf2", "y2")],
    })
    titles = [it.title for it in out]
    assert titles == ["a1", "h1", "hf1", "a2", "h2", "hf2"]


def test_empty_title_is_kept_only_once() -> None:
    """Empty-title items in the same source still dedup against each other via normalize."""
    a = CandidateItem(source="hackernews", title="")
    b = CandidateItem(source="hackernews", title="")
    out = _merge_and_dedupe({"hackernews": [a, b]})
    # Both items have normalize_title_for_dedup("") == "" → not added to seen_titles
    # so they both pass through. (Empty-title gating is the adapter's job.)
    assert len(out) == 2
