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
)


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
    ))

    assert summary.reviewed_count == 1
    assert summary.accepted_count == 0
    assert summary.rejected_count == 1
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

    assert result.reviewed_count >= 8
    assert result.candidates
    assert all(candidate.url.startswith("https://") for candidate in result.candidates)
