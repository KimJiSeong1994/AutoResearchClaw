"""Characterization tests: exact traveler judgement behavior, frozen before extraction.

These pin what `decide_evidence` and the static source portfolio do TODAY, at
concrete values rather than by property. Their whole job is to fail if moving
these constants into `runtime/traveler-scoring.json` changes any decision the
traveler makes. They are deliberately literal — a characterization test that
recomputes the formula it guards proves nothing.

Do not "fix" a failure here by updating the expected number. A change to these
values changes which papers the traveler collects in production.
"""

from __future__ import annotations

import pytest

from discord_openclaw_bridge.traveler_evidence import (
    EvidenceDecision,
    ExtractedEvidence,
    FetchResult,
    decide_evidence,
)
from discord_openclaw_bridge.traveler_source_discovery import StaticTechnicalSourceProvider


def ok_fetch(url: str = "https://example.com/a") -> FetchResult:
    return FetchResult(status="ok", url=url, body="<html>body</html>", http_status=200)


def extracted(**kwargs: object) -> ExtractedEvidence:
    base: dict[str, object] = {"extractor": "html", "title": "A paper"}
    base.update(kwargs)
    return ExtractedEvidence(**base)  # type: ignore[arg-type]


# (matched_keywords, item_count, published_or_updated) -> exact confidence_score
CONFIDENCE_CASES = (
    ([], 0, "", 0.6),
    (["rag"], 0, "", 0.65),
    (["rag", "retrieval"], 0, "", 0.7),
    (["rag", "retrieval", "graph"], 0, "", 0.75),
    (["a", "b", "c", "d"], 0, "", 0.8),
    (["a", "b", "c", "d", "e"], 0, "", 0.85),
    (["a", "b", "c", "d", "e", "f"], 0, "", 0.85),          # keyword bonus caps at +0.25
    (["a", "b", "c", "d", "e", "f", "g", "h"], 0, "", 0.85),  # still capped
    ([], 3, "", 0.7),
    ([], 0, "2026-01-01", 0.65),
    ([], 3, "2026-01-01", 0.75),
    (["rag"], 3, "2026-01-01", 0.8),
    (["a", "b", "c", "d", "e"], 9, "2026-01-01", 0.95),      # ceiling
    (["a", "b", "c", "d", "e", "f", "g"], 9, "2026-01-01", 0.95),
)


@pytest.mark.parametrize("keywords,item_count,published,expected", CONFIDENCE_CASES)
def test_accepted_confidence_score_is_exact(
    keywords: list[str], item_count: int, published: str, expected: float
) -> None:
    decision = decide_evidence(
        ok_fetch(),
        extracted(matched_keywords=list(keywords), item_count=item_count, published_or_updated=published),
        topic="",
    )
    assert decision.candidate_state == "accepted"
    assert decision.confidence_score == expected
    assert decision.reason == "bounded_public_evidence_observed"


def test_confidence_never_exceeds_ceiling() -> None:
    decision = decide_evidence(
        ok_fetch(),
        extracted(matched_keywords=[str(i) for i in range(50)], item_count=999, published_or_updated="2026-01-01"),
        topic="",
    )
    assert decision.confidence_score == 0.95


REJECTION_CASES = (
    (FetchResult(status="blocked", url="https://x/a", reason="robots"), "robots", "blocked"),
    (FetchResult(status="blocked", url="https://x/a"), "blocked_by_policy", "blocked"),
    (FetchResult(status="error", url="https://x/a", reason="timeout"), "timeout", "fetch_failed"),
    (FetchResult(status="error", url="https://x/a"), "fetch_failed", "fetch_failed"),
)


@pytest.mark.parametrize("fetch,reason,rejection_class", REJECTION_CASES)
def test_fetch_failures_reject_with_exact_reason(fetch: FetchResult, reason: str, rejection_class: str) -> None:
    decision = decide_evidence(fetch, extracted(), topic="")
    assert decision.candidate_state == "rejected"
    assert decision.reason == reason
    assert decision.rejection_class == rejection_class
    assert decision.confidence_score == 0.0


def test_no_extractable_metadata_rejects() -> None:
    decision = decide_evidence(ok_fetch(), ExtractedEvidence(extractor="html"), topic="")
    assert decision == EvidenceDecision(
        candidate_state="rejected",
        reason="no_extractable_public_metadata",
        rejection_class="no_evidence",
    )


def test_topic_terms_without_keyword_match_rejects_as_low_relevance() -> None:
    decision = decide_evidence(ok_fetch(), extracted(matched_keywords=[]), topic="retrieval augmented generation")
    assert decision == EvidenceDecision(
        candidate_state="rejected",
        reason="no_topic_relevance_evidence",
        rejection_class="low_relevance",
    )


def test_empty_topic_skips_the_relevance_gate() -> None:
    """An empty topic must not reject: the gate keys off topic terms existing."""
    decision = decide_evidence(ok_fetch(), extracted(matched_keywords=[]), topic="")
    assert decision.candidate_state == "accepted"


# Frozen portfolio. Adding or removing a source changes what the traveler
# discovers when arXiv and Semantic Scholar rate-limit and static is the fallback.
EXPECTED_SOURCE_URLS = frozenset({
    "https://arxiv.org/list/cs.AI/recent",
    "https://arxiv.org/list/cs.CL/recent",
    "https://openai.com/research/",
    "https://research.google/blog/",
    "https://www.anthropic.com/research",
    "https://huggingface.co/papers",
    "https://paperswithcode.com/latest",
    "https://openreview.net/",
    "https://www.microsoft.com/en-us/research/blog/",
    "https://ai.meta.com/research/",
    "https://aclanthology.org/2026.eacl-long.8/",
    "https://arxiv.org/abs/2603.19281",
    "https://research.ibm.com/publications/an-analysis-of-hyper-parameter-optimization-methods-for-retrieval-augmented-generation",
    "https://ojs.aaai.org/index.php/AAAI/article/view/40265",
    "https://arxiv.org/abs/2511.18194",
})

EXPECTED_TYPE_COUNTS = {"research_lab_blog": 5, "paper_page": 5, "conference_feed": 3, "article_hub": 2}


def test_static_portfolio_urls_are_frozen() -> None:
    urls = {row[1] for row in StaticTechnicalSourceProvider._SOURCES}
    assert urls == EXPECTED_SOURCE_URLS
    assert len(StaticTechnicalSourceProvider._SOURCES) == 15


def test_static_portfolio_type_mix_is_frozen() -> None:
    counts: dict[str, int] = {}
    for row in StaticTechnicalSourceProvider._SOURCES:
        counts[row[2]] = counts.get(row[2], 0) + 1
    assert counts == EXPECTED_TYPE_COUNTS


def test_static_portfolio_rows_are_well_formed() -> None:
    for row in StaticTechnicalSourceProvider._SOURCES:
        title, url, source_type, reliability = row
        assert title and url.startswith("https://") and source_type and reliability
