"""Unit tests for ``sources/canonical.py`` -- 12+ cases per design section 7.4."""

from __future__ import annotations

import pytest

from paper_recommender.sources import CandidateItem
from paper_recommender.sources.canonical import (
    arxiv_url_to_id,
    nature_url_to_doi,
    openreview_url_to_forum_id,
    to_canonical_key,
)


# ---------------------------------------------------------------------------
# nature_url_to_doi
# ---------------------------------------------------------------------------


def test_nature_doi_standard_slug() -> None:
    url = "https://www.nature.com/articles/s41586-024-12345-6"
    assert nature_url_to_doi(url) == "10.1038/s41586-024-12345-6"


def test_nature_doi_no_www_prefix() -> None:
    url = "https://nature.com/articles/s41586-024-12345-6"
    assert nature_url_to_doi(url) == "10.1038/s41586-024-12345-6"


def test_nature_doi_wrong_host_returns_none() -> None:
    url = "https://example.com/articles/s41586-024-12345-6"
    assert nature_url_to_doi(url) is None


def test_nature_doi_collection_page_returns_none() -> None:
    """Nature collection/search pages do not match the slug pattern."""
    url = "https://www.nature.com/nature/articles?type=article"
    assert nature_url_to_doi(url) is None


def test_nature_doi_d_prefix_news_article() -> None:
    """d-prefix slugs (news/editorial) map to 10.1038/d... DOIs."""
    url = "https://www.nature.com/articles/d41586-024-00123-4"
    assert nature_url_to_doi(url) == "10.1038/d41586-024-00123-4"


def test_nature_doi_arbitrary_slug_returns_none() -> None:
    """Non-DOI slugs like 'the-best-paper' must not match."""
    url = "https://www.nature.com/articles/the-best-paper"
    assert nature_url_to_doi(url) is None


# ---------------------------------------------------------------------------
# arxiv_url_to_id
# ---------------------------------------------------------------------------


def test_arxiv_abs_path() -> None:
    url = "https://arxiv.org/abs/2401.12345"
    assert arxiv_url_to_id(url) == "2401.12345"


def test_arxiv_pdf_path() -> None:
    url = "https://arxiv.org/pdf/2401.12345v2"
    assert arxiv_url_to_id(url) == "2401.12345v2"


def test_arxiv_html_path() -> None:
    url = "https://arxiv.org/html/2401.12345"
    assert arxiv_url_to_id(url) == "2401.12345"


def test_arxiv_export_host() -> None:
    url = "https://export.arxiv.org/abs/2401.12345"
    assert arxiv_url_to_id(url) == "2401.12345"


def test_arxiv_wrong_host_returns_none() -> None:
    url = "https://example.com/abs/2401.12345"
    assert arxiv_url_to_id(url) is None


# ---------------------------------------------------------------------------
# openreview_url_to_forum_id
# ---------------------------------------------------------------------------


def test_openreview_forum_id() -> None:
    url = "https://openreview.net/forum?id=ABC123xyz"
    assert openreview_url_to_forum_id(url) == "ABC123xyz"


def test_openreview_pdf_returns_none() -> None:
    """Only /forum path is matched, not /pdf."""
    url = "https://openreview.net/pdf?id=ABC123xyz"
    assert openreview_url_to_forum_id(url) is None


def test_openreview_wrong_host_returns_none() -> None:
    url = "https://example.com/forum?id=ABC123xyz"
    assert openreview_url_to_forum_id(url) is None


# ---------------------------------------------------------------------------
# to_canonical_key -- priority ordering
# ---------------------------------------------------------------------------


def test_key_arxiv_id_field_wins() -> None:
    """arxiv_id field takes priority over url and doi."""
    item = CandidateItem(
        source="arxiv",
        title="Paper A",
        arxiv_id="2401.00001",
        doi="10.1234/x",
        url="https://www.nature.com/articles/s41586-024-12345-6",
    )
    assert to_canonical_key(item) == "arxiv:2401.00001"


def test_key_doi_field_second_priority() -> None:
    """doi field used when arxiv_id is absent."""
    item = CandidateItem(
        source="manual",
        title="Paper B",
        doi="10.1234/ABC",
        url="https://www.nature.com/articles/s41586-024-12345-6",
    )
    assert to_canonical_key(item) == "doi:10.1234/abc"


def test_key_url_derived_arxiv() -> None:
    """URL-derived arxiv ID used when no explicit arxiv_id/doi."""
    item = CandidateItem(
        source="manual",
        title="Paper C",
        url="https://arxiv.org/abs/2401.00099",
    )
    assert to_canonical_key(item) == "arxiv:2401.00099"


def test_key_url_derived_nature_doi() -> None:
    """URL-derived nature DOI used when no explicit id and url is nature."""
    item = CandidateItem(
        source="miner",
        title="Paper D",
        url="https://www.nature.com/articles/s41586-024-12345-6",
    )
    assert to_canonical_key(item) == "doi:10.1038/s41586-024-12345-6"


def test_key_url_derived_openreview() -> None:
    """URL-derived openreview id used when no explicit id and url is openreview."""
    item = CandidateItem(
        source="manual",
        title="Paper E",
        url="https://openreview.net/forum?id=MyForum99",
    )
    assert to_canonical_key(item) == "openreview:myforum99"


def test_key_title_fallback() -> None:
    """Title normalization fallback when no structured id or mappable url."""
    item = CandidateItem(
        source="hackernews",
        title="Show HN: My Cool Tool!",
    )
    assert to_canonical_key(item) == "title:my cool tool"


def test_key_all_blank_returns_empty_string() -> None:
    """All blank -> empty string key (not deduplicated)."""
    item = CandidateItem(source="hackernews", title="")
    assert to_canonical_key(item) == ""


def test_key_arxiv_id_lowercased() -> None:
    """Keys are always lowercased for case-insensitive dedup."""
    item = CandidateItem(source="arxiv", title="Paper", arxiv_id="2401.ABCD")
    assert to_canonical_key(item) == "arxiv:2401.abcd"


def test_frozen_invariant_not_violated() -> None:
    """to_canonical_key must not mutate the frozen CandidateItem."""
    item = CandidateItem(
        source="arxiv",
        title="Immutable Paper",
        arxiv_id="2401.99999",
    )
    key = to_canonical_key(item)
    assert key == "arxiv:2401.99999"
    # Verify the item is unchanged
    assert item.arxiv_id == "2401.99999"
    assert item.title == "Immutable Paper"
    with pytest.raises((TypeError, AttributeError)):
        item.arxiv_id = "mutated"  # type: ignore[misc]
