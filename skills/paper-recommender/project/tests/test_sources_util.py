from __future__ import annotations

from pathlib import Path

from paper_recommender.sources import CandidateItem
from paper_recommender.sources._util import (
    clean_text,
    item_matches_topics,
    normalize_title_for_dedup,
    redacted_path,
)


def test_empty_or_none_returns_empty_string() -> None:
    assert normalize_title_for_dedup(None) == ""
    assert normalize_title_for_dedup("") == ""
    assert normalize_title_for_dedup("   ") == ""


def test_case_insensitive() -> None:
    assert normalize_title_for_dedup("Hello World") == normalize_title_for_dedup("hello world")
    assert normalize_title_for_dedup("HELLO WORLD") == normalize_title_for_dedup("hello world")


def test_strips_show_hn_prefix() -> None:
    assert (
        normalize_title_for_dedup("Show HN: My LLM Tool")
        == normalize_title_for_dedup("show hn: my llm tool")
        == normalize_title_for_dedup("My LLM Tool")
    )


def test_strips_ask_hn_and_d_tags() -> None:
    assert normalize_title_for_dedup("Ask HN: How do I X?") == normalize_title_for_dedup("How do I X")
    assert normalize_title_for_dedup("[D] Discussion topic") == normalize_title_for_dedup("Discussion topic")


def test_collapses_whitespace_variants() -> None:
    a = normalize_title_for_dedup("Show HN:  My  Tool")
    b = normalize_title_for_dedup("show hn: my\ttool")
    c = normalize_title_for_dedup("Show HN:\nmy tool")
    assert a == b == c == "my tool"


def test_strips_punctuation() -> None:
    assert normalize_title_for_dedup("Title!") == normalize_title_for_dedup("Title?")
    assert normalize_title_for_dedup("A: B (C) — D") == "a b c d"


def test_does_not_collapse_meaningful_content_words() -> None:
    """Sanity check: titles with different content do not collide."""
    a = normalize_title_for_dedup("Transformer attention is all you need")
    b = normalize_title_for_dedup("BERT pretraining objective")
    assert a != b


def test_unicode_letters_preserved() -> None:
    """Korean / non-ASCII content words must survive the punctuation pass."""
    assert "트랜스포머" in normalize_title_for_dedup("트랜스포머 어텐션!")


def test_only_one_prefix_stripped() -> None:
    """A title that happens to contain Show HN: in the middle is NOT stripped twice."""
    out = normalize_title_for_dedup("Show HN: a paper about Show HN: phenomenon")
    assert out.startswith("a paper")
    assert "show hn" in out  # the inner one survives (after punctuation strip)


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("  hello\n\tworld  ") == "hello world"
    assert clean_text(None) == ""


def test_item_matches_topics_can_include_tags() -> None:
    item = CandidateItem(
        source="manual",
        title="Unrelated title",
        url="https://example.com",
        abstract="No keyword here",
        authors=(),
        year=2026,
        venue="Example",
        tags=("agentic-ai",),
    )
    assert not item_matches_topics(item, ["agentic-ai"])
    assert item_matches_topics(item, ["agentic-ai"], include_tags=True)


def test_redacted_path_uses_basename_only() -> None:
    assert redacted_path(Path("/private/user/secret/news.mbox")) == ".../news.mbox"
