from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import discord_openclaw_bridge.miner as miner_module
from discord_openclaw_bridge.miner import (
    DiscordLinkMetadata,
    expand_collection_links,
    extract_urls,
    record_message_links,
    record_miner_link,
    record_requested_links,
    render_ack,
    sanitize_url,
)


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_sanitize_url_strips_secret_and_tracking_query_params() -> None:
    url = sanitize_url(
        "<https://Example.COM/article?utm_source=news&token=SECRET&ok=1&midtoken=x#private-fragment>."
    )

    assert url == "https://example.com/article?ok=1"


def test_extract_urls_deduplicates_sanitized_links() -> None:
    urls = extract_urls(
        "검토 요청 https://example.com/a?utm_source=discord 과 <https://example.com/a>. "
        "그리고 https://example.com/b)."
    )

    assert urls == ["https://example.com/a", "https://example.com/b"]


def test_record_miner_link_writes_intake_and_claw_review_queue(tmp_path: Path) -> None:
    intake_path = tmp_path / "intake" / "links.jsonl"
    review_path = tmp_path / "review" / "queue.jsonl"
    created_at = datetime(2026, 5, 5, 1, 2, 3, tzinfo=timezone.utc)

    result = record_miner_link(
        url="https://example.com/research?utm_campaign=x&ok=1",
        title="Useful Agent Report",
        note="뉴스레터 후보로 검토 요청",
        intake_path=intake_path,
        review_queue_path=review_path,
        discord=DiscordLinkMetadata(guild_id=1, channel_id=2, message_id=3, user_id=4),
        created_at=created_at,
    )

    assert result.accepted
    assert result.intake_id.startswith("miner_")

    intake_rows = _read_jsonl(intake_path)
    review_rows = _read_jsonl(review_path)
    assert intake_rows == review_rows
    row = intake_rows[0]
    assert row["agent"] == "jiphyeonjeon-miner"
    assert row["reviewer"] == "jiphyeonjeon-claw"
    assert row["status"] == "pending_claw_review"
    assert row["review"]["newsletter_reflection"] == "blocked_until_approved"
    assert row["url"] == "https://example.com/research?ok=1"
    assert row["source"] == "discord_miner"
    assert row["summary"] == "뉴스레터 후보로 검토 요청"
    assert row["discord"] == {"guild_id": 1, "channel_id": 2, "message_id": 3, "user_id": 4}


def test_record_miner_link_is_idempotent_by_sanitized_url(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    first = record_miner_link(
        url="https://example.com/rag-agent?utm_source=discord",
        intake_path=intake_path,
        review_queue_path=review_path,
    )
    second = record_miner_link(
        url="https://example.com/rag-agent",
        title="New title",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert first.accepted
    assert second.duplicate
    assert len(_read_jsonl(intake_path)) == 1
    assert len(_read_jsonl(review_path)) == 1


def test_record_message_links_renders_discord_ack(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    results = record_message_links(
        message_text=(
            "검토 부탁 https://example.com/rag-agent "
            "https://example.com/rag-agent?utm_source=x https://example.com/llm-benchmark"
        ),
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert [result.status for result in results] == ["accepted", "accepted"]
    assert "링크 2개" in render_ack(results)
    assert "집현전-클로 검토 큐" in render_ack(results)


def test_expand_collection_links_extracts_alphaxiv_abs_links(monkeypatch) -> None:
    monkeypatch.setattr(
        miner_module,
        "_fetch_public_html",
        lambda _url: """
        <a href="/abs/2605.02881">paper</a>
        <a href="/overview/2605.02881">overview</a>
        <a href="/abs/2605.02881">duplicate</a>
        <a href="https://github.com/example/repo">repo</a>
        <a href="/abs/on-policy-distillation">paper</a>
        """,
    )

    links = expand_collection_links("https://www.alphaxiv.org/?sort=Hot")

    assert links == ["https://www.alphaxiv.org/abs/2605.02881", "https://www.alphaxiv.org/abs/on-policy-distillation"]


def test_record_message_links_expands_the_batch_index(monkeypatch, tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"
    monkeypatch.setattr(
        miner_module,
        "_fetch_public_html",
        lambda _url: """
        <a href="/the-batch/issue-351">Issue 351</a>
        <a href="/the-batch/tag/research">Research tag</a>
        <a href="/the-batch/issue-350">Issue 350</a>
        """,
    )

    results = record_message_links(
        message_text="검토 부탁 https://www.deeplearning.ai/the-batch/",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert [result.url for result in results] == [
        "https://www.deeplearning.ai/the-batch/issue-351",
        "https://www.deeplearning.ai/the-batch/issue-350",
    ]
    assert [result.status for result in results] == ["accepted", "accepted"]
    assert len(_read_jsonl(intake_path)) == 2


def test_expand_collection_links_keeps_d_prefix_nature_slug(monkeypatch) -> None:
    """d-prefix news/editorial slugs (e.g. d41586-...) must be kept."""
    monkeypatch.setattr(
        miner_module,
        "_fetch_public_html",
        lambda _url: """
        <a href="/articles/d41586-024-00123-4">News piece</a>
        <a href="/articles/s41586-024-12345-6">Research paper</a>
        """,
    )
    links = expand_collection_links("https://www.nature.com/nature/articles?type=article")
    assert "https://www.nature.com/articles/d41586-024-00123-4" in links
    assert "https://www.nature.com/articles/s41586-024-12345-6" in links


def test_expand_collection_links_rejects_arbitrary_nature_slug(monkeypatch) -> None:
    """Arbitrary slugs like 'the-best-paper' must not pass the narrow regex."""
    monkeypatch.setattr(
        miner_module,
        "_fetch_public_html",
        lambda _url: """
        <a href="/articles/the-best-paper">Blog post</a>
        <a href="/articles/some-random-article-title">Another blog</a>
        <a href="/articles/s41586-024-12345-6">Real paper</a>
        """,
    )
    links = expand_collection_links("https://www.nature.com/nature/articles?type=article")
    # Only the structured DOI slug passes
    assert links == ["https://www.nature.com/articles/s41586-024-12345-6"]


def test_record_message_links_expands_nature_article_index(monkeypatch, tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"
    monkeypatch.setattr(
        miner_module,
        "_fetch_public_html",
        lambda _url: """
        <a href="/articles/s41586-026-00123-4">Nature paper</a>
        <a href="/nature/articles?type=article&page=2">Next page</a>
        <a href="/articles/s41586-026-00123-4">duplicate</a>
        <a href="/news/example">News</a>
        <a href="https://www.nature.com/articles/s41586-026-00567-8">Second paper</a>
        """,
    )

    results = record_message_links(
        message_text="검토 부탁 https://www.nature.com/nature/articles?type=article",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert [result.url for result in results] == [
        "https://www.nature.com/articles/s41586-026-00123-4",
        "https://www.nature.com/articles/s41586-026-00567-8",
    ]
    assert [result.status for result in results] == ["accepted", "accepted"]
    assert len(_read_jsonl(intake_path)) == 2


def test_record_requested_links_expands_collection_url(monkeypatch, tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"
    monkeypatch.setattr(miner_module, "_fetch_public_html", lambda _url: '<a href="/abs/2605.02881">paper</a>')

    results = record_requested_links(
        url="https://www.alphaxiv.org/?sort=Hot",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert len(results) == 1
    assert results[0].url == "https://www.alphaxiv.org/abs/2605.02881"
    assert results[0].accepted


def test_record_miner_link_rejects_non_academic_non_technical_links(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://www.linkedin.com/jobs/view/123",
        title="Job Alert",
        note="채용공고 추천",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.rejected
    assert not intake_path.exists()
    assert not review_path.exists()
    assert "수집 제외" in render_ack([result])


def test_record_miner_link_does_not_accept_allowlist_host_in_query(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://example.com/post?next=arxiv.org",
        title="Generic market note",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.rejected
    assert not intake_path.exists()


def test_record_miner_link_rejects_slack_links_even_with_technical_terms(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://openclaw.slack.com/archives/C123/p456",
        title="LLM agent benchmark discussion",
        note="technical research thread",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.rejected
    assert not intake_path.exists()
    assert not review_path.exists()


def test_record_miner_link_accepts_privacy_security_evaluation_reports(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://example.com/research/privacy-evaluation-benchmark",
        title="Privacy evaluation benchmark for LLM agents",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.accepted
    assert len(_read_jsonl(intake_path)) == 1


def test_record_miner_link_with_summary_keyword(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://arxiv.org/abs/2401.00001",
        title="Test Paper Title",
        summary="Abstract text extracted from article body.",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.accepted
    row = _read_jsonl(intake_path)[0]
    assert row["summary"] == "Abstract text extracted from article body."


def test_record_miner_link_with_published_at_keyword(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://arxiv.org/abs/2401.00002",
        title="Another Paper",
        published_at="2024-01-15",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.accepted
    row = _read_jsonl(intake_path)[0]
    assert row["published_at"] == "2024-01-15"


def test_record_miner_link_summary_overrides_note(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://arxiv.org/abs/2401.00003",
        title="Override Paper",
        note="Manual note from Discord user",
        summary="Fetched abstract overrides manual note",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.accepted
    row = _read_jsonl(intake_path)[0]
    assert row["summary"] == "Fetched abstract overrides manual note"


def test_record_miner_link_falls_back_to_note(tmp_path: Path) -> None:
    intake_path = tmp_path / "links.jsonl"
    review_path = tmp_path / "queue.jsonl"

    result = record_miner_link(
        url="https://arxiv.org/abs/2401.00004",
        title="Fallback Paper",
        note="Fallback note when no summary provided",
        intake_path=intake_path,
        review_queue_path=review_path,
    )

    assert result.accepted
    row = _read_jsonl(intake_path)[0]
    assert row["summary"] == "Fallback note when no summary provided"


# ---------------------------------------------------------------------------
# SSRF redirect guard (security review fix)
# ---------------------------------------------------------------------------


def _stub_redirect_args(start_url: str, target_url: str):
    """Build the positional args HTTPRedirectHandler.redirect_request expects.

    Uses io.BytesIO so the temp-file cleanup hook in CPython's tempfile
    module (HTTPError keeps a reference to the fp) finds a real .close()
    and does not emit a PytestUnraisableExceptionWarning.
    """
    import io

    from urllib.request import Request

    req = Request(start_url, headers={"User-Agent": "test"})
    return req, io.BytesIO(b""), 302, "Found", {}, target_url


def test_safe_redirect_handler_blocks_aws_metadata_target() -> None:
    """SSRF guard: a 3xx Location pointing at AWS IMDSv1 must be rejected."""
    from urllib.error import HTTPError

    handler = miner_module._SafeRedirectHandler()
    args = _stub_redirect_args(
        "https://www.nature.com/articles/s41586-024-00001-0",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    )

    try:
        handler.redirect_request(*args)
    except HTTPError as exc:
        assert exc.code == 302
        assert "redirect blocked" in (exc.reason or str(exc.msg) or "").lower()
    else:
        raise AssertionError("expected redirect to private metadata IP to raise HTTPError")


def test_safe_redirect_handler_blocks_rfc1918_target() -> None:
    """SSRF guard: a 3xx Location pointing at an internal LAN host must be rejected."""
    from urllib.error import HTTPError

    handler = miner_module._SafeRedirectHandler()
    args = _stub_redirect_args(
        "https://www.nature.com/articles/s41586-024-00001-0",
        "http://10.0.0.42/admin",
    )

    try:
        handler.redirect_request(*args)
    except HTTPError as exc:
        assert exc.code == 302
    else:
        raise AssertionError("expected redirect to RFC1918 address to raise HTTPError")


def test_safe_redirect_handler_allows_public_https_target() -> None:
    """SSRF guard must still let legitimate http→https / canonical redirects through."""
    handler = miner_module._SafeRedirectHandler()
    args = _stub_redirect_args(
        "http://www.nature.com/articles/s41586-024-00001-0",
        "https://www.nature.com/articles/s41586-024-00001-0",
    )

    redirected = handler.redirect_request(*args)

    assert redirected is not None
    assert redirected.full_url.startswith("https://www.nature.com/")
