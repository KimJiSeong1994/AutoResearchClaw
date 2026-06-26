from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from discord_openclaw_bridge.traveler import (
    TravelerResearchRequest,
    TravelerSourceInput,
    record_research_request,
    record_source_candidate,
)
from discord_openclaw_bridge.traveler_source_discovery import (
    DiscoveryCandidate,
    DiscoveryProviderResult,
    ResearchRequest,
    StaticTechnicalSourceProvider,
    discover_sources,
    load_pending_requests,
    _get_with_backoff,
)
from discord_openclaw_bridge.traveler_scout import create_scout_requests, load_scout_topics


class FakeProvider:
    name = "fake-provider"

    def __init__(self, candidates: list[DiscoveryCandidate], *, error: str | None = None) -> None:
        self.candidates = candidates
        self.error = error
        self.requests: list[ResearchRequest] = []

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        self.requests.append(request)
        return DiscoveryProviderResult(
            provider=self.name,
            reviewed_count=request.min_sources_to_review,
            candidates=self.candidates,
            rejected=["bad source"] if self.error else [],
            error=self.error,
        )


class LowReviewProvider(FakeProvider):
    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        self.requests.append(request)
        return DiscoveryProviderResult(provider=self.name, reviewed_count=1, candidates=self.candidates)


class ErrorProvider:
    name = "error-provider"

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:  # noqa: ARG002
        return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error="rate limited", error_kind="rate_limited", rejected=["provider rate limited"])


class ParseErrorProvider:
    name = "parse-error-provider"

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:  # noqa: ARG002
        return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error="JSON parse failed", error_kind="parse", rejected=["provider parse failed"])


class RequestAwareProvider:
    name = "request-aware-provider"

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:  # noqa: ARG002
        return DiscoveryProviderResult(
            provider=self.name,
            reviewed_count=request.min_sources_to_review,
            candidates=[
                DiscoveryCandidate(
                    url=f"https://example.com/{request.request_id}",
                    title=f"Source for {request.request_id}",
                    source_type="research_lab_blog",
                    reliability_note="Public recurring page.",
                    cadence_note="Recent updates.",
                    topic_fit=request.topic,
                    collection_hint="poll_public_blog",
                    provider=self.name,
                )
            ],
        )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_load_pending_requests_filters_non_pending_rows(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation sources", min_sources_to_review=5),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    with research.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"status": "completed", "topic": "ignore"}) + "\n")
        handle.write(json.dumps({"status": "pending_deep_research", "topic": ""}) + "\n")

    requests = load_pending_requests(research, default_candidate_queue=candidates)

    assert len(requests) == 1
    assert requests[0].topic == "RAG evaluation sources"
    assert requests[0].min_sources_to_review == 10
    assert requests[0].candidate_queue_path == candidates


def test_candidate_queue_path_outside_configured_review_dir_falls_back(tmp_path: Path) -> None:
    research = tmp_path / "review" / "research.jsonl"
    configured_candidates = tmp_path / "review" / "source-candidates.jsonl"
    hostile_candidates = tmp_path / "manual_links" / "approved-manual-links.jsonl"
    research.parent.mkdir(parents=True)
    research.write_text(
        json.dumps(
            {
                "request_id": "req-hostile",
                "status": "pending_deep_research",
                "topic": "RAG sources",
                "candidate_queue_path": str(hostile_candidates),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    requests = load_pending_requests(research, default_candidate_queue=configured_candidates)

    assert len(requests) == 1
    assert requests[0].candidate_queue_path == configured_candidates


def test_missing_request_id_uses_stable_digest(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "source-candidates.jsonl"
    research.write_text(
        json.dumps({"status": "pending_deep_research", "topic": "RAG sources"}) + "\n",
        encoding="utf-8",
    )

    first = load_pending_requests(research, default_candidate_queue=candidates)[0].request_id
    second = load_pending_requests(research, default_candidate_queue=candidates)[0].request_id

    assert first == second
    assert first.startswith("traveler_request_")


def test_discovery_records_provider_candidates_through_traveler_queue(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    status = tmp_path / "status.json"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation sources", min_sources_to_review=12),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = FakeProvider(
        [
            DiscoveryCandidate(
                url="https://example.com/research?utm_source=x&id=7",
                title="Example Research",
                source_type="research_lab_blog",
                reliability_note="Official recurring public research page.",
                cadence_note="Recent posts observed in provider metadata.",
                topic_fit="RAG evaluation.",
                collection_hint="poll_public_blog",
                provider="fake-provider",
            )
        ]
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        status_path=status,
        deep_research=False,
    ))

    rows = _jsonl(candidates)
    assert summary.accepted_count == 1
    assert summary.reviewed_count == 12
    assert provider.requests[0].min_sources_to_review == 12
    assert rows[0]["agent"] == "jiphyeonjeon-traveler"
    assert rows[0]["status"] == "pending_source_review"
    assert rows[0]["review"]["miner_seed_expansion"] == "blocked_until_reviewed"
    assert rows[0]["url"] == "https://example.com/research?id=7"
    assert "fake-provider" in rows[0]["topic_fit"]
    assert json.loads(status.read_text(encoding="utf-8"))["accepted_count"] == 1
    assert _jsonl(research)[0]["status"] == "completed_source_discovery"


def test_discovery_enforces_per_request_max_candidates(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation sources", min_sources_to_review=10, max_candidates=1),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = FakeProvider(
        [
            DiscoveryCandidate(
                url=f"https://example.com/source-{idx}",
                title=f"Source {idx}",
                source_type="research_lab_blog",
                reliability_note="Public source.",
                cadence_note="Recurring.",
                topic_fit="RAG evaluation.",
                collection_hint="poll",
            )
            for idx in range(3)
        ]
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        max_candidates=10,
        deep_research=False,
    ))

    rows = _jsonl(candidates)
    request_rows = _jsonl(research)
    assert summary.accepted_count == 1
    assert len(rows) == 1
    assert request_rows[0]["max_candidates"] == 1
    assert request_rows[0]["processed_summary"]["max_candidates"] == 1


def test_scout_requests_do_not_starve_later_topics_across_daily_runs(monkeypatch: Any, tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    scout = tmp_path / "scout-candidates.jsonl"
    topics = load_scout_topics(None)
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_DISCOVERY_MAX_REQUESTS", "3")

    create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout)
    first = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=scout,
        providers=[RequestAwareProvider()],
        max_candidates=20,
        deep_research=False,
    ))
    create_scout_requests(topics=topics, research_queue_path=research, scout_queue_path=scout)
    second = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=scout,
        providers=[RequestAwareProvider()],
        max_candidates=20,
        deep_research=False,
    ))

    request_rows = _jsonl(research)
    completed_topics = {
        row.get("scout_topic_id")
        for row in request_rows
        if row.get("status") == "completed_source_discovery" and row.get("scout_topic_id")
    }
    assert first.requests_processed == 3
    assert second.requests_processed == 3
    assert {topic.topic_id for topic in topics}.issubset(completed_topics)


def test_discovery_deduplicates_existing_queue_and_rejects_private_urls(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="AI systems sources"),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    record_source_candidate(TravelerSourceInput(url="https://example.com/source", title="Existing"), queue_path=candidates)
    provider = FakeProvider(
        [
            DiscoveryCandidate(
                url="https://example.com/source?utm_campaign=x",
                title="Duplicate",
                source_type="article_hub",
                reliability_note="Public source.",
                cadence_note="Recurring.",
                topic_fit="AI.",
                collection_hint="poll",
            ),
            DiscoveryCandidate(
                url="http://127.0.0.1/private",
                title="Private",
                source_type="article_hub",
                reliability_note="Bad.",
                cadence_note="Bad.",
                topic_fit="AI.",
                collection_hint="poll",
            ),
            DiscoveryCandidate(
                url="https://new.example.com/feed",
                title="New",
                source_type="rss",
                reliability_note="Public feed.",
                cadence_note="Daily.",
                topic_fit="AI.",
                collection_hint="rss",
            ),
        ]
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        deep_research=False,
    ))

    rows = _jsonl(candidates)
    assert summary.duplicate_count == 1
    assert summary.rejected_count == 1
    assert summary.accepted_count == 1
    assert len(rows) == 2
    assert rows[-1]["url"] == "https://new.example.com/feed"


def test_discovery_dry_run_does_not_append_candidates(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="language model sources"),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = FakeProvider(
        [
            DiscoveryCandidate(
                url="https://example.com/feed",
                title="Example Feed",
                source_type="rss",
                reliability_note="Public feed.",
                cadence_note="Daily.",
                topic_fit="LLM.",
                collection_hint="rss",
            )
        ]
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        dry_run=True,
        deep_research=False,
    ))

    assert summary.accepted_count == 1
    assert not candidates.exists()


def test_discovery_dry_run_validates_private_urls(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="language model sources"),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = FakeProvider(
        [
            DiscoveryCandidate(
                url="http://127.0.0.1/private",
                title="Private",
                source_type="rss",
                reliability_note="Bad.",
                cadence_note="Bad.",
                topic_fit="LLM.",
                collection_hint="rss",
            )
        ]
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        dry_run=True,
        deep_research=False,
    ))

    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
    assert not candidates.exists()


def test_discovery_requires_many_sources_before_recording(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="language model sources", min_sources_to_review=20),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = LowReviewProvider(
        [
            DiscoveryCandidate(
                url="https://example.com/feed",
                title="Example Feed",
                source_type="rss",
                reliability_note="Public feed.",
                cadence_note="Daily.",
                topic_fit="LLM.",
                collection_hint="rss",
            )
        ]
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        dry_run=True,
        deep_research=False,
    ))

    assert summary.reviewed_count == 1
    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
    assert not candidates.exists()


def test_discovery_allows_evidence_backed_static_fallback_when_network_providers_rate_limit(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation benchmark retrieval augmented generation", min_sources_to_review=20),
        queue_path=research,
        candidate_queue_path=candidates,
    )

    def fetcher(url: str) -> FetchResult:
        if "aclanthology" in url:
            return FetchResult(
                status="ok",
                url=url,
                canonical_url=url,
                content_type="text/html",
                bytes_read=140,
                body="<html><head><title>T2-RAGBench RAG Evaluation Benchmark</title></head></html>",
            )
        return FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=80,
            body="<html><head><title>Unrelated</title></head></html>",
        )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[ErrorProvider(), StaticTechnicalSourceProvider()],
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=fetcher,
    ))

    rows = _jsonl(candidates)
    assert summary.error_count == 1
    assert summary.reviewed_count >= 10
    assert summary.accepted_count == 1
    assert rows[0]["url"] == "https://aclanthology.org/2026.eacl-long.8/"


def test_discovery_does_not_fallback_for_parse_provider_errors(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation benchmark retrieval augmented generation", min_sources_to_review=20),
        queue_path=research,
        candidate_queue_path=candidates,
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[ParseErrorProvider(), StaticTechnicalSourceProvider()],
        deep_research=True,
        evidence_path=tmp_path / "evidence.jsonl",
        evidence_fetcher=lambda url: FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=140,
            body="<html><head><title>T2-RAGBench RAG Evaluation Benchmark</title></head></html>",
        ),
    ))

    assert summary.accepted_count == 0
    assert summary.error_count == 1
    assert not candidates.exists()


def test_discovery_does_not_fallback_when_retryable_and_parse_errors_mix(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation benchmark retrieval augmented generation", min_sources_to_review=20),
        queue_path=research,
        candidate_queue_path=candidates,
    )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[ErrorProvider(), ParseErrorProvider(), StaticTechnicalSourceProvider()],
        deep_research=True,
        evidence_path=tmp_path / "evidence.jsonl",
        evidence_fetcher=lambda url: FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=140,
            body="<html><head><title>T2-RAGBench RAG Evaluation Benchmark</title></head></html>",
        ),
    ))

    assert summary.accepted_count == 0
    assert summary.error_count == 2
    assert not candidates.exists()


def test_static_provider_reviews_many_public_sources(tmp_path: Path) -> None:
    async def run_provider() -> DiscoveryProviderResult:
        request = ResearchRequest(
            request_id="req1",
            topic="AI papers",
            scope="public recurring sources",
            min_sources_to_review=10,
            candidate_queue_path=tmp_path / "candidates.jsonl",
        )
        async with httpx.AsyncClient() as client:
            return await StaticTechnicalSourceProvider().discover(request, client=client)

    result = asyncio.run(run_provider())

    assert result.reviewed_count >= 6
    assert result.candidates
    assert all(candidate.url.startswith("https://") for candidate in result.candidates)
    assert result.reviewed_count == len({candidate.url for candidate in result.candidates})


def test_static_provider_includes_topic_specific_paper_pages(tmp_path: Path) -> None:
    async def run_provider() -> DiscoveryProviderResult:
        request = ResearchRequest(
            request_id="req-rag",
            topic="RAG evaluation benchmark retrieval augmented generation",
            scope="public recurring sources",
            min_sources_to_review=10,
            candidate_queue_path=tmp_path / "candidates.jsonl",
        )
        async with httpx.AsyncClient() as client:
            return await StaticTechnicalSourceProvider().discover(request, client=client)

    result = asyncio.run(run_provider())

    urls = {candidate.url for candidate in result.candidates}
    assert "https://aclanthology.org/2026.eacl-long.8/" in urls
    assert "https://arxiv.org/abs/2603.19281" in urls
    assert any(candidate.source_type == "paper_page" for candidate in result.candidates)
    assert all(
        candidate.next_action == "review_paper_lead_for_recurring_source"
        for candidate in result.candidates
        if candidate.source_type == "paper_page"
    )


def test_static_provider_skips_unrelated_paper_pages(tmp_path: Path) -> None:
    async def run_provider() -> DiscoveryProviderResult:
        request = ResearchRequest(
            request_id="req-db",
            topic="database storage engine concurrency control",
            scope="public recurring sources",
            min_sources_to_review=10,
            candidate_queue_path=tmp_path / "candidates.jsonl",
        )
        async with httpx.AsyncClient() as client:
            return await StaticTechnicalSourceProvider().discover(request, client=client)

    result = asyncio.run(run_provider())

    assert all(candidate.source_type != "paper_page" for candidate in result.candidates)
    assert "T2-RAGBench" not in {candidate.title for candidate in result.candidates}


def test_static_provider_skips_paper_pages_for_sparse_topic(tmp_path: Path) -> None:
    async def run_provider() -> DiscoveryProviderResult:
        request = ResearchRequest(
            request_id="req-ai",
            topic="AI",
            scope="public recurring sources",
            min_sources_to_review=10,
            candidate_queue_path=tmp_path / "candidates.jsonl",
        )
        async with httpx.AsyncClient() as client:
            return await StaticTechnicalSourceProvider().discover(request, client=client)

    result = asyncio.run(run_provider())

    assert all(candidate.source_type != "paper_page" for candidate in result.candidates)


def test_provider_get_retries_once_for_rate_limit(monkeypatch: Any) -> None:
    calls = 0
    sleeps: list[float] = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.request = httpx.Request("GET", "https://example.com/search")

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                response = httpx.Response(self.status_code, request=self.request)
                raise httpx.HTTPStatusError("rate limited", request=self.request, response=response)

    class FakeClient:
        async def get(self, _url: str, *, params: dict[str, str]) -> FakeResponse:
            nonlocal calls
            calls += 1
            return FakeResponse(429 if calls == 1 else 200)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    response = asyncio.run(_get_with_backoff(FakeClient(), "https://example.com/search", params={"q": "rag"}, provider="fake", attempts=2))  # type: ignore[arg-type]

    assert response.status_code == 200
    assert calls == 2
    assert sleeps == [0.5]


def test_provider_get_honors_retry_after_header(monkeypatch: Any) -> None:
    calls = 0
    sleeps: list[float] = []

    class FakeResponse:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code
            self.request = httpx.Request("GET", "https://example.com/search")
            self.headers = {"Retry-After": "1.25"}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                response = httpx.Response(self.status_code, headers=self.headers, request=self.request)
                raise httpx.HTTPStatusError("rate limited", request=self.request, response=response)

    class FakeClient:
        async def get(self, _url: str, *, params: dict[str, str]) -> FakeResponse:
            nonlocal calls
            calls += 1
            return FakeResponse(429 if calls == 1 else 200)

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    response = asyncio.run(_get_with_backoff(FakeClient(), "https://example.com/search", params={"q": "rag"}, provider="fake", attempts=2))  # type: ignore[arg-type]

    assert response.status_code == 200
    assert calls == 2
    assert sleeps == [1.25]


def test_load_pending_requests_skips_live_test_requests(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "source-candidates.jsonl"
    research.write_text(
        json.dumps({
            "request_id": "traveler_request_test",
            "status": "pending_deep_research",
            "topic": "LIVE TEST - 집현전 여행자 연결 검증",
            "requester_note": "safe to ignore",
        }) + "\n" +
        json.dumps({
            "request_id": "traveler_request_real",
            "status": "pending_deep_research",
            "topic": "AI research engineering sources",
        }) + "\n",
        encoding="utf-8",
    )

    requests = load_pending_requests(research, default_candidate_queue=candidates)

    assert [request.request_id for request in requests] == ["traveler_request_real"]

from discord_openclaw_bridge.traveler_evidence import (
    FetchResult,
    append_evidence,
    extract_evidence,
    fetch_public_evidence,
)


def test_traveler_evidence_blocks_private_urls() -> None:
    result = fetch_public_evidence("http://127.0.0.1/private")

    assert result.status == "blocked"
    assert result.reason == "non_public_or_unsafe_url"


def test_traveler_evidence_extracts_html_metadata() -> None:
    fetch = FetchResult(
        status="ok",
        url="https://example.com/research",
        canonical_url="https://example.com/research",
        content_type="text/html; charset=utf-8",
        bytes_read=120,
        body='<html><head><title>RAG Evaluation Report</title><meta name="description" content="Research benchmark for RAG evaluation"></head></html>',
    )

    extracted = extract_evidence(fetch, topic="RAG evaluation")

    assert extracted.extractor == "html_metadata_v1"
    assert extracted.title == "RAG Evaluation Report"
    assert "rag" in extracted.matched_keywords
    assert "evaluation" in extracted.matched_keywords


def test_traveler_evidence_writer_rejects_full_html(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"

    try:
        append_evidence(evidence, {"summary": "<html>full body</html>"})
    except ValueError as exc:
        assert "full HTML" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected full HTML evidence rejection")


def test_discovery_deep_research_requires_evidence_before_recording(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation sources", min_sources_to_review=10),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = FakeProvider([
        DiscoveryCandidate(
            url="https://example.com/research",
            title="Example Research",
            source_type="research_lab_blog",
            reliability_note="Official public page.",
            cadence_note="Updated.",
            topic_fit="RAG evaluation.",
            collection_hint="poll_public_blog",
            provider="fake-provider",
        )
    ])

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=lambda url: FetchResult(status="failed", url=url, reason="network unavailable"),
    ))

    assert summary.accepted_count == 0
    assert summary.evidence_count == 1
    assert summary.evidence_rejected_count == 1
    assert not candidates.exists()
    evidence_rows = _jsonl(evidence)
    assert evidence_rows[0]["decision"]["candidate_state"] == "rejected"
    assert "body" not in json.dumps(evidence_rows[0]).lower()


def test_discovery_deep_research_promotes_evidence_backed_candidate(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    status = tmp_path / "status.json"
    record_research_request(
        TravelerResearchRequest(topic="RAG evaluation sources", min_sources_to_review=10),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = FakeProvider([
        DiscoveryCandidate(
            url="https://example.com/research?utm_source=x",
            title="Example Research",
            source_type="research_lab_blog",
            reliability_note="Official public page.",
            cadence_note="Updated.",
            topic_fit="RAG evaluation.",
            collection_hint="poll_public_blog",
            provider="fake-provider",
        )
    ])

    def fetcher(url: str) -> FetchResult:
        return FetchResult(
            status="ok",
            url=url,
            canonical_url="https://example.com/research",
            content_type="text/html",
            bytes_read=160,
            body='<html><head><title>RAG Evaluation Research</title><meta name="description" content="Public RAG evaluation research updates"></head></html>',
        )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[provider],
        status_path=status,
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=fetcher,
    ))

    rows = _jsonl(candidates)
    evidence_rows = _jsonl(evidence)
    status_payload = json.loads(status.read_text(encoding="utf-8"))
    assert summary.accepted_count == 1
    assert summary.evidence_count == 1
    assert rows[0]["evidence"]["status"] == "fetched"
    assert rows[0]["evidence"]["id"] == evidence_rows[0]["evidence_id"]
    assert rows[0]["review"]["miner_seed_expansion"] == "blocked_until_reviewed"
    assert status_payload["deep_research_enabled"] is True
    assert status_payload["evidence_count"] == 1


def test_traveler_discovery_cli_defaults_to_env_deep_research(monkeypatch: Any, tmp_path: Path) -> None:
    import discord_openclaw_bridge.traveler_source_discovery as module

    calls: list[dict[str, Any]] = []

    async def fake_discover_sources(**kwargs: Any):
        calls.append(kwargs)
        return module.DiscoveryRunSummary(
            requests_seen=0,
            requests_processed=0,
            providers_used=[],
            reviewed_count=0,
            accepted_count=0,
            duplicate_count=0,
            rejected_count=0,
            error_count=0,
            candidate_queue_path=str(tmp_path / "candidates.jsonl"),
            deep_research_enabled=True,
        )

    monkeypatch.setattr(module, "discover_sources", fake_discover_sources)
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH", str(tmp_path / "status.json"))

    module.main([])

    assert calls
    assert calls[0]["deep_research"] is None


def test_traveler_discovery_cli_can_explicitly_disable_deep_research(monkeypatch: Any, tmp_path: Path) -> None:
    import discord_openclaw_bridge.traveler_source_discovery as module

    calls: list[dict[str, Any]] = []

    async def fake_discover_sources(**kwargs: Any):
        calls.append(kwargs)
        return module.DiscoveryRunSummary(
            requests_seen=0,
            requests_processed=0,
            providers_used=[],
            reviewed_count=0,
            accepted_count=0,
            duplicate_count=0,
            rejected_count=0,
            error_count=0,
            candidate_queue_path=str(tmp_path / "candidates.jsonl"),
            deep_research_enabled=False,
        )

    monkeypatch.setattr(module, "discover_sources", fake_discover_sources)
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH", str(tmp_path / "status.json"))

    module.main(["--no-deep-research"])

    assert calls
    assert calls[0]["deep_research"] is False


def test_discovery_propagates_autonomous_scout_metadata_to_candidate(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    scout_candidates = tmp_path / "scout-candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    research.write_text(
        json.dumps(
            {
                "request_id": "traveler_request_scout",
                "status": "pending_deep_research",
                "topic": "LLM agents research engineering",
                "min_sources_to_review": 10,
                "candidate_queue_path": str(scout_candidates),
                "discovery_mode": "autonomous_scout",
                "scout_topic_id": "llm_agents",
                "scout_priority": "high",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    provider = FakeProvider([
        DiscoveryCandidate(
            url="https://example.com/agents",
            title="Agent Research",
            source_type="research_lab_blog",
            reliability_note="Official public page.",
            cadence_note="Updated.",
            topic_fit="LLM agents.",
            collection_hint="poll_public_blog",
            provider="fake-provider",
        )
    ])

    def fetcher(url: str) -> FetchResult:
        return FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=120,
            body='<html><head><title>LLM Agents Research</title><meta name="description" content="LLM agents research updates"></head></html>',
        )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=scout_candidates,
        providers=[provider],
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=fetcher,
    ))

    rows = _jsonl(scout_candidates)
    assert summary.accepted_count == 1
    assert rows[0]["discovery_mode"] == "autonomous_scout"
    assert rows[0]["scout_topic_id"] == "llm_agents"
    assert rows[0]["scout_priority"] == "high"
    assert "autonomous-scout" in rows[0]["tags"]


def test_discovery_propagates_paperwiki_provenance_to_candidate(tmp_path: Path) -> None:
    topics_config = tmp_path / "topics.json"
    topics_config.write_text(
        json.dumps(
            {
                "topics": [
                    {
                        "id": "llm_agents",
                        "query": "LLM agents research engineering",
                        "priority": "high",
                        "source": "interest-note",
                        "paperwiki_interest_slug": "llm-agents",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    research = tmp_path / "research.jsonl"
    scout_candidates = tmp_path / "source-candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    create_scout_requests(
        topics=load_scout_topics(topics_config),
        research_queue_path=research,
        scout_queue_path=scout_candidates,
    )
    provider = FakeProvider([
        DiscoveryCandidate(
            url="https://example.com/agents",
            title="Agent Research",
            source_type="research_lab_blog",
            reliability_note="Official public page.",
            cadence_note="Updated.",
            topic_fit="LLM agents.",
            collection_hint="poll_public_blog",
            provider="fake-provider",
        )
    ])

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=scout_candidates,
        providers=[provider],
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=lambda url: FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=120,
            body='<html><head><title>LLM Agents Research</title><meta name="description" content="LLM agents research updates"></head></html>',
        ),
    ))

    rows = _jsonl(scout_candidates)
    assert summary.accepted_count == 1
    assert rows[0]["topic_source"] == "interest-note"
    assert rows[0]["paperwiki_interest_slug"] == "llm-agents"
    assert "paperwiki-influenced" in rows[0]["tags"]


def test_static_curated_source_with_low_keyword_match_still_reaches_review_queue(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "source-candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    record_research_request(
        TravelerResearchRequest(
            topic="LLM agents research engineering",
            min_sources_to_review=10,
            max_candidates=1,
            discovery_mode="autonomous_scout",
            scout_topic_id="llm_agents",
        ),
        queue_path=research,
        candidate_queue_path=candidates,
    )

    def fetcher(url: str) -> FetchResult:
        return FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=120,
            body="<html><head><title>Public recurring research updates</title></head></html>",
        )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[StaticTechnicalSourceProvider()],
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=fetcher,
    ))

    rows = _jsonl(candidates)
    evidence_rows = _jsonl(evidence)
    assert summary.accepted_count == 1
    assert rows[0]["status"] == "pending_source_review"
    assert rows[0]["evidence"]["status"] == "fetched"
    assert evidence_rows[0]["decision"]["reason"] == "curated_static_source_surface_requires_review"


def test_requested_static_source_with_low_keyword_match_stays_rejected(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "source-candidates.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="LLM agents research engineering", min_sources_to_review=10, max_candidates=1),
        queue_path=research,
        candidate_queue_path=candidates,
    )

    def fetcher(url: str) -> FetchResult:
        return FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=120,
            body="<html><head><title>Public recurring updates</title></head></html>",
        )

    summary = asyncio.run(discover_sources(
        research_queue_path=research,
        default_candidate_queue_path=candidates,
        providers=[StaticTechnicalSourceProvider()],
        deep_research=True,
        evidence_path=evidence,
        evidence_fetcher=fetcher,
    ))

    assert summary.accepted_count == 0
    assert not candidates.exists()
    assert _jsonl(evidence)[0]["decision"]["candidate_state"] != "accepted"


def test_autonomous_static_exception_requires_static_provider_review_type_and_ok_fetch(tmp_path: Path) -> None:
    def low_relevance_fetcher(url: str) -> FetchResult:
        return FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            content_type="text/html",
            bytes_read=120,
            body="<html><head><title>Public recurring updates</title></head></html>",
        )

    def failed_fetcher(url: str) -> FetchResult:
        return FetchResult(status="failed", url=url, canonical_url=url, reason="network unavailable")

    cases = [
        (
            "non_static_provider",
            FakeProvider([
                DiscoveryCandidate(
                    url="https://example.com/non-static",
                    title="Recurring updates",
                    source_type="research_lab_blog",
                    reliability_note="Public recurring page.",
                    cadence_note="Updated.",
                    topic_fit="Agent research updates.",
                    collection_hint="poll_public_blog",
                    provider="fake-provider",
                )
            ]),
            low_relevance_fetcher,
        ),
        (
            "non_review_source_type",
            FakeProvider([
                DiscoveryCandidate(
                    url="https://example.com/paper",
                    title="Paper page",
                    source_type="paper_page",
                    reliability_note="Public page.",
                    cadence_note="Updated.",
                    topic_fit="Agent research updates.",
                    collection_hint="manual_review",
                    provider="static-technical-sources",
                )
            ]),
            low_relevance_fetcher,
        ),
        (
            "failed_fetch",
            StaticTechnicalSourceProvider(),
            failed_fetcher,
        ),
    ]

    for name, provider, fetcher in cases:
        case_dir = tmp_path / name
        case_dir.mkdir()
        research = case_dir / "research.jsonl"
        candidates = case_dir / "source-candidates.jsonl"
        evidence = case_dir / "evidence.jsonl"
        record_research_request(
            TravelerResearchRequest(
                topic="LLM agents research engineering",
                min_sources_to_review=10,
                max_candidates=1,
                discovery_mode="autonomous_scout",
                scout_topic_id="llm_agents",
            ),
            queue_path=research,
            candidate_queue_path=candidates,
        )

        summary = asyncio.run(discover_sources(
            research_queue_path=research,
            default_candidate_queue_path=candidates,
            providers=[provider],
            deep_research=True,
            evidence_path=evidence,
            evidence_fetcher=fetcher,
        ))

        assert summary.accepted_count == 0, name
        assert not candidates.exists(), name
        assert _jsonl(evidence)[0]["decision"].get("reason") != "curated_static_source_surface_requires_review"
