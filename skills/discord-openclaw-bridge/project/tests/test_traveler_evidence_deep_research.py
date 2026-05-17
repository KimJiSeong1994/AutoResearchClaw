from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from discord_openclaw_bridge import traveler_evidence
from discord_openclaw_bridge.traveler import TravelerResearchRequest, record_research_request
from discord_openclaw_bridge.traveler_source_discovery import (
    DiscoveryCandidate,
    DiscoveryProviderResult,
    ResearchRequest,
    discover_sources,
)
from discord_openclaw_bridge.post_traveler_collection_report import (
    CollectionContext,
    build_report_items,
    format_report_body,
)


class EvidenceProvider:
    name = "evidence-provider"

    def __init__(self, candidates: list[DiscoveryCandidate], *, reviewed_count: int = 20) -> None:
        self.candidates = candidates
        self.reviewed_count = reviewed_count

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        return DiscoveryProviderResult(
            provider=self.name,
            reviewed_count=self.reviewed_count,
            candidates=self.candidates,
        )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _candidate(
    url: str,
    *,
    title: str = "Evidence Candidate",
    reliability_note: str = "Official public page.",
    cadence_note: str = "Recent public updates observed.",
) -> DiscoveryCandidate:
    return DiscoveryCandidate(
        url=url,
        title=title,
        source_type="research_lab_blog",
        reliability_note=reliability_note,
        cadence_note=cadence_note,
        topic_fit="AI systems evidence.",
        collection_hint="poll_public_page",
        provider="evidence-provider",
    )


def test_safe_evidence_fetch_blocks_private_urls_before_http_request(monkeypatch: Any) -> None:
    requested: list[str] = []

    def fail_if_called(*args: object, **kwargs: object) -> object:
        requested.append(str(args[0]) if args else "called")
        raise AssertionError("private URLs must be rejected before any HTTP request is attempted")

    monkeypatch.setattr(traveler_evidence, "_safe_evidence_url_open", fail_if_called)

    result = traveler_evidence.fetch_public_evidence("http://127.0.0.1/private")

    assert requested == []
    assert result.status == "blocked"
    assert result.reason == "non_public_or_unsafe_url"
    assert result.body == ""


def test_evidence_extracts_summary_without_persisting_full_html(tmp_path: Path) -> None:
    private_marker = "SECRET_TOKEN_SHOULD_NOT_BE_PERSISTED"
    html = f"""
    <html>
      <head>
        <title>Verified Research Blog</title>
        <meta name="description" content="Short public evidence summary from the page metadata.">
      </head>
      <body><main><p>Public first paragraph.</p><script>{private_marker}</script></main></body>
    </html>
    """
    fetch = traveler_evidence.FetchResult(
        status="ok",
        url="https://research.example.com/blog",
        canonical_url="https://research.example.com/blog",
        http_status=200,
        content_type="text/html; charset=utf-8",
        bytes_read=len(html),
        body=html,
    )

    extracted = traveler_evidence.extract_evidence(fetch, topic="research blog")
    decision = traveler_evidence.decide_evidence(fetch, extracted, topic="research blog")
    record = traveler_evidence.build_evidence_record(
        request_id="traveler_request_test",
        lead_id="traveler_lead_test",
        provider="test-provider",
        query="research blog",
        url="https://research.example.com/blog",
        fetch=fetch,
        extracted=extracted,
        decision=decision,
    )
    evidence_path = tmp_path / "evidence.jsonl"
    traveler_evidence.append_evidence(evidence_path, record)
    serialized = evidence_path.read_text(encoding="utf-8")

    assert decision.candidate_state == "accepted"
    assert record["fetch"]["status"] == "ok"
    assert record["extract"]["title"] == "Verified Research Blog"
    assert "Short public evidence summary" in record["extract"]["summary_excerpt"]
    assert private_marker not in serialized
    assert "<html" not in serialized.lower()
    assert "body" not in record["fetch"]


def test_deep_discovery_rejects_no_evidence_candidates_and_records_evidence_backed_candidate(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="evidence-backed AI research sources", min_sources_to_review=20),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = EvidenceProvider(
        [
            _candidate(
                "https://empty.example.com/blog",
                title="No Evidence Blog",
                reliability_note="Provider metadata only.",
                cadence_note="Provider cadence only.",
            ),
            _candidate("https://verified.example.com/research", title="Verified Research"),
        ]
    )

    def fetch_evidence(url: str) -> traveler_evidence.FetchResult:
        if url == "https://verified.example.com/research":
            body = (
                "<html><head><title>Verified Research</title>"
                "<meta name=\"description\" content=\"Public page metadata confirms recurring evidence-backed AI research updates.\">"
                "</head><body></body></html>"
            )
            return traveler_evidence.FetchResult(
                status="ok",
                url=url,
                canonical_url=url,
                http_status=200,
                content_type="text/html",
                bytes_read=len(body),
                body=body,
            )
        return traveler_evidence.FetchResult(
            status="ok",
            url=url,
            canonical_url=url,
            http_status=200,
            content_type="text/html",
            bytes_read=0,
            body="",
        )

    summary = asyncio.run(
        discover_sources(
            research_queue_path=research,
            default_candidate_queue_path=candidates,
            providers=[provider],
            deep_research=True,
            evidence_fetcher=fetch_evidence,
            evidence_path=tmp_path / "evidence.jsonl",
        )
    )

    rows = _jsonl(candidates)
    assert summary.accepted_count == 1
    assert summary.rejected_count == 1
    assert summary.evidence_count == 2
    assert summary.evidence_rejected_count == 1
    assert [row["url"] for row in rows] == ["https://verified.example.com/research"]
    assert rows[0]["evidence"]["status"] == "fetched"
    assert rows[0]["evidence"]["summary"] == "Public page metadata confirms recurring evidence-backed AI research updates."
    assert rows[0]["evidence"]["confidence"] > 0
    assert "provider metadata only" not in rows[0]["reliability_rationale"].lower()


def test_deep_discovery_rejects_private_evidence_candidates_without_persisting_queue(tmp_path: Path) -> None:
    research = tmp_path / "research.jsonl"
    candidates = tmp_path / "candidates.jsonl"
    record_research_request(
        TravelerResearchRequest(topic="private evidence should be blocked", min_sources_to_review=20),
        queue_path=research,
        candidate_queue_path=candidates,
    )
    provider = EvidenceProvider([_candidate("http://169.254.169.254/latest/meta-data", title="Metadata Service")])

    def fetch_evidence(url: str) -> traveler_evidence.FetchResult:
        assert url == "http://169.254.169.254/latest/meta-data"
        return traveler_evidence.FetchResult(status="blocked", url=url, reason="non_public_or_unsafe_url")

    summary = asyncio.run(
        discover_sources(
            research_queue_path=research,
            default_candidate_queue_path=candidates,
            providers=[provider],
            deep_research=True,
            evidence_fetcher=fetch_evidence,
            evidence_path=tmp_path / "evidence.jsonl",
        )
    )

    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
    assert summary.evidence_rejected_count == 1
    assert not candidates.exists()


def test_traveler_collection_report_surfaces_evidence_status() -> None:
    rows = [
        {
            "title": "Verified Research Lab",
            "url": "https://research.example.com/blog",
            "source_type": "research_lab_blog",
            "reliability_rationale": "Official lab page.",
            "update_cadence_evidence": "Recent updates observed.",
            "evidence": {
                "status": "fetched",
                "summary": "Public metadata confirms recurring research updates.",
                "confidence": 0.8,
            },
            "topic_fit": "AI systems reports.",
            "access_constraints": "public_http_evidence_verified",
            "recommended_next_action": "review_for_miner_seed",
            "status": "pending_source_review",
        }
    ]
    context = CollectionContext(seed_urls=set(), seed_hosts=set(), collected_urls=set(), collected_hosts=set())

    items = build_report_items(rows, context)
    body = format_report_body(items)

    assert len(items) == 1
    assert items[0].evidence_status == "fetched"
    assert items[0].evidence_summary == "Public metadata confirms recurring research updates."
    assert "**증거 상태:** fetched / confidence=0.80" in body
    assert "public_http_evidence_verified" in body


def test_safe_evidence_fetch_blocks_legacy_loopback_numeric_host() -> None:
    result = traveler_evidence.fetch_public_evidence("http://2130706433/")

    assert result.status in {"blocked", "failed"}


def test_safe_evidence_fetch_blocks_dns_to_private_host(monkeypatch: Any) -> None:
    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[tuple[object, object, object, object, tuple[str, int]]]:
        assert host == "research.example.com"
        return [(object(), object(), object(), object(), ("127.0.0.1", 0))]

    monkeypatch.setattr(traveler_evidence.socket, "getaddrinfo", fake_getaddrinfo)
    result = traveler_evidence.fetch_public_evidence("https://research.example.com/blog")

    assert result.status == "failed"
    assert result.reason == "non_public_resolved_host"


def test_irrelevant_fetchable_page_is_not_promoted() -> None:
    fetch = traveler_evidence.FetchResult(
        status="ok",
        url="https://example.com/cooking",
        canonical_url="https://example.com/cooking",
        http_status=200,
        content_type="text/html",
        bytes_read=120,
        body='<html><head><title>Cooking Blog</title><meta name="description" content="Pasta recipes and kitchen tools"></head></html>',
    )

    extracted = traveler_evidence.extract_evidence(fetch, topic="RAG evaluation")
    decision = traveler_evidence.decide_evidence(fetch, extracted, topic="RAG evaluation")

    assert extracted.matched_keywords == []
    assert decision.candidate_state == "rejected"
    assert decision.rejection_class == "low_relevance"


def test_generic_research_page_is_not_promoted_for_specific_scout_topic() -> None:
    fetch = traveler_evidence.FetchResult(
        status="ok",
        url="https://example.com/research",
        canonical_url="https://example.com/research",
        http_status=200,
        content_type="text/html",
        bytes_read=120,
        body='<html><head><title>Research Blog</title><meta name="description" content="Official public research updates"></head></html>',
    )

    extracted = traveler_evidence.extract_evidence(fetch, topic="LLM agents research engineering")
    decision = traveler_evidence.decide_evidence(fetch, extracted, topic="LLM agents research engineering")

    assert extracted.matched_keywords == []
    assert decision.candidate_state == "rejected"
    assert decision.rejection_class == "low_relevance"


def test_html_without_meta_description_does_not_persist_body_or_script_text() -> None:
    marker = "SECRET_TOKEN_SHOULD_NOT_BE_PERSISTED"
    fetch = traveler_evidence.FetchResult(
        status="ok",
        url="https://example.com/research",
        canonical_url="https://example.com/research",
        http_status=200,
        content_type="text/html",
        bytes_read=120,
        body=f"<html><head><title>RAG Evaluation</title></head><body><script>{marker}</script><p>body secret</p></body></html>",
    )

    extracted = traveler_evidence.extract_evidence(fetch, topic="RAG evaluation")
    decision = traveler_evidence.decide_evidence(fetch, extracted, topic="RAG evaluation")
    record = traveler_evidence.build_evidence_record(
        request_id="traveler_request_test",
        lead_id="traveler_lead_test",
        provider="test-provider",
        query="RAG evaluation",
        url="https://example.com/research",
        fetch=fetch,
        extracted=extracted,
        decision=decision,
    )
    serialized = json.dumps(record, ensure_ascii=False)

    assert extracted.title == "RAG Evaluation"
    assert extracted.summary_excerpt == ""
    assert decision.candidate_state == "accepted"
    assert marker not in serialized
    assert "body secret" not in serialized
