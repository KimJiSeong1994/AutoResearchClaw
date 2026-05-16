"""Discover public source candidates for 집현전-여행자 before the daily report.

The discovery runner is intentionally conservative: it searches only public
metadata endpoints, validates every candidate through the existing Traveler
candidate writer, and never mutates Miner seeds or approved links.  The daily
report can then compare the refreshed source-candidate queue against the
current collection surface.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import quote_plus

import httpx

from .miner import clean_text, read_jsonl, sanitize_url
from .traveler import (
    TravelerRecordResult,
    TravelerSourceInput,
    build_source_candidate_record,
    default_research_queue_path,
    default_source_queue_path,
    record_source_candidate,
)

LOG = logging.getLogger(__name__)
DEFAULT_DISCOVERY_STATE_PATH = Path.home() / ".openclaw" / "workspace" / "state" / "traveler-source-discovery-last-status.json"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
REQUEST_STATUS_PENDING = "pending_deep_research"


@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    topic: str
    scope: str
    min_sources_to_review: int
    candidate_queue_path: Path


@dataclass(frozen=True)
class DiscoveryCandidate:
    url: str
    title: str
    source_type: str
    reliability_note: str
    cadence_note: str
    topic_fit: str
    collection_hint: str
    access_constraints: str = "public_metadata_endpoint"
    next_action: str = "review_for_miner_seed"
    provider: str = "unknown"


@dataclass(frozen=True)
class DiscoveryProviderResult:
    provider: str
    reviewed_count: int
    candidates: list[DiscoveryCandidate] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class DiscoveryRunSummary:
    requests_seen: int
    requests_processed: int
    providers_used: list[str]
    reviewed_count: int
    accepted_count: int
    duplicate_count: int
    rejected_count: int
    error_count: int
    candidate_queue_path: str
    status_path: str | None = None


class DiscoveryProvider(Protocol):
    name: str

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        ...


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [row for row in read_jsonl(path) if isinstance(row, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("could not read jsonl %s: %s", path, exc)
        return []


def _safe_candidate_queue_path(raw_path: object, *, default_candidate_queue: Path) -> Path:
    default_path = default_candidate_queue.expanduser()
    if not raw_path:
        return default_path
    candidate = Path(str(raw_path)).expanduser()
    try:
        default_dir = default_path.parent.resolve(strict=False)
        candidate_path = candidate.resolve(strict=False)
    except OSError:
        return default_path
    if candidate_path == default_path.resolve(strict=False) or default_dir in candidate_path.parents:
        return candidate_path
    LOG.warning(
        "ignoring traveler candidate_queue_path outside configured review directory raw=%s default=%s",
        raw_path,
        default_path,
    )
    return default_path


def _request_from_row(row: dict[str, Any], *, default_candidate_queue: Path) -> ResearchRequest | None:
    if row.get("status") != REQUEST_STATUS_PENDING:
        return None
    topic = clean_text(row.get("topic"), limit=200)
    if not topic:
        return None
    generated_id = "traveler_request_" + hashlib.sha256(topic.encode("utf-8")).hexdigest()[:16]
    request_id = clean_text(row.get("request_id") or generated_id, limit=120)
    scope = clean_text(row.get("scope") or "high-trust recurring technical sources", limit=300)
    try:
        min_sources = int(row.get("min_sources_to_review") or 20)
    except (TypeError, ValueError):
        min_sources = 20
    min_sources = max(10, min(min_sources, 80))
    candidate_path = _safe_candidate_queue_path(row.get("candidate_queue_path"), default_candidate_queue=default_candidate_queue)
    return ResearchRequest(
        request_id=request_id,
        topic=topic,
        scope=scope,
        min_sources_to_review=min_sources,
        candidate_queue_path=candidate_path,
    )


def load_pending_requests(path: Path, *, default_candidate_queue: Path) -> list[ResearchRequest]:
    return [
        request
        for row in _read_jsonl_rows(path)
        if (request := _request_from_row(row, default_candidate_queue=default_candidate_queue)) is not None
    ]


def _keywords(topic: str, *, limit: int = 8) -> list[str]:
    words: list[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}|[가-힣]{2,}", topic):
        word = raw.lower()
        if word in {"and", "the", "for", "with", "from", "into", "using", "source", "sources"}:
            continue
        if word not in words:
            words.append(word)
    return words[:limit]


def _topic_fit(topic: str, title: str) -> str:
    keys = _keywords(topic, limit=5)
    matched = [key for key in keys if key.lower() in title.lower()]
    if matched:
        return f"요청 주제와 겹치는 공개 메타데이터 키워드: {', '.join(matched)}."
    return f"요청 주제 `{clean_text(topic, limit=120)}`의 후보 출처로 추가 샘플 검토가 필요합니다."


def _dedupe_candidates(candidates: Iterable[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    seen: set[str] = set()
    deduped: list[DiscoveryCandidate] = []
    for candidate in candidates:
        safe_url = sanitize_url(candidate.url)
        key = safe_url or f"invalid:{candidate.url}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


class ArxivDiscoveryProvider:
    name = "arxiv-api"

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        params = {
            "search_query": f"all:{request.topic}",
            "start": "0",
            "max_results": str(min(max(request.min_sources_to_review, 10), 50)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        try:
            response = await client.get(ARXIV_API_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=str(exc), rejected=["arXiv API request failed"])

        reviewed = 0
        candidates: list[DiscoveryCandidate] = []
        rejected: list[str] = []
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=f"arXiv XML parse failed: {exc}")
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            reviewed += 1
            title = clean_text(" ".join((entry.findtext("atom:title", default="", namespaces=ns) or "").split()), limit=180)
            categories = [node.attrib.get("term", "") for node in entry.findall("atom:category", ns)]
            primary = categories[0] if categories else ""
            if not primary:
                rejected.append(f"{title or 'untitled'}: missing arXiv category")
                continue
            url = f"https://arxiv.org/list/{primary}/recent"
            candidates.append(
                DiscoveryCandidate(
                    url=url,
                    title=f"arXiv {primary} recent submissions",
                    source_type="conference_feed",
                    reliability_note="arXiv 공식 공개 API에서 최근 논문 메타데이터가 확인된 분야별 recent 목록입니다.",
                    cadence_note="arXiv submittedDate 정렬 결과에서 최근 항목이 확인되어 반복 갱신 후보입니다.",
                    topic_fit=_topic_fit(request.topic, title),
                    collection_hint="poll_recent_arxiv_category",
                    provider=self.name,
                )
            )
        return DiscoveryProviderResult(provider=self.name, reviewed_count=reviewed, candidates=_dedupe_candidates(candidates), rejected=rejected)


class SemanticScholarDiscoveryProvider:
    name = "semantic-scholar-graph"

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        params = {
            "query": request.topic,
            "limit": str(min(max(request.min_sources_to_review, 10), 50)),
            "fields": "title,venue,year,externalIds,openAccessPdf,url",
        }
        try:
            response = await client.get(SEMANTIC_SCHOLAR_SEARCH_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=str(exc), rejected=["Semantic Scholar API request failed"])
        try:
            payload = response.json()
        except ValueError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=f"Semantic Scholar JSON parse failed: {exc}")
        data = payload.get("data", []) if isinstance(payload, dict) else []
        reviewed = 0
        candidates: list[DiscoveryCandidate] = []
        rejected: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            reviewed += 1
            title = clean_text(item.get("title"), limit=180)
            external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
            arxiv_id = clean_text(external.get("ArXiv"), limit=80)
            venue = clean_text(item.get("venue"), limit=120)
            if arxiv_id:
                url = f"https://arxiv.org/search/?query={quote_plus(request.topic)}&searchtype=all&source=header"
                source_type = "archive_page"
                title_text = f"arXiv search: {request.topic}"
                cadence = "Semantic Scholar 검색 결과에 arXiv 식별자가 포함되어 공개 아카이브 검색면을 반복 확인할 수 있습니다."
            elif venue:
                url = f"https://www.semanticscholar.org/search?q={quote_plus(venue)}&sort=relevance"
                source_type = "article_hub"
                title_text = f"Semantic Scholar venue search: {venue}"
                cadence = "Semantic Scholar 공개 검색 결과에서 venue 단위 후속 논문을 반복 확인할 수 있습니다."
            else:
                rejected.append(f"{title or 'untitled'}: no reusable public source surface")
                continue
            candidates.append(
                DiscoveryCandidate(
                    url=url,
                    title=title_text,
                    source_type=source_type,
                    reliability_note="Semantic Scholar Graph API의 공개 논문 메타데이터 검색 결과에서 도출한 반복 검색면입니다.",
                    cadence_note=cadence,
                    topic_fit=_topic_fit(request.topic, title),
                    collection_hint="review_public_scholar_search_surface",
                    provider=self.name,
                )
            )
        return DiscoveryProviderResult(provider=self.name, reviewed_count=reviewed, candidates=_dedupe_candidates(candidates), rejected=rejected)


class StaticTechnicalSourceProvider:
    """Static portfolio of public, recurring technical source surfaces.

    This prevents a network/API outage from making the daily report look as if
    no discovery was attempted.  The entries are still candidates only and stay
    blocked behind Claw review.
    """

    name = "static-technical-sources"
    _SOURCES = (
        ("arXiv cs.AI recent submissions", "https://arxiv.org/list/cs.AI/recent", "conference_feed", "arXiv official recent list for AI papers."),
        ("arXiv cs.CL recent submissions", "https://arxiv.org/list/cs.CL/recent", "conference_feed", "arXiv official recent list for computational linguistics papers."),
        ("OpenAI Research", "https://openai.com/research/", "research_lab_blog", "Official public research announcements and papers."),
        ("Google Research Blog", "https://research.google/blog/", "research_lab_blog", "Official public research blog with recurring technical posts."),
        ("Anthropic Research", "https://www.anthropic.com/research", "research_lab_blog", "Official public research publication surface."),
        ("Hugging Face Papers", "https://huggingface.co/papers", "article_hub", "Public daily paper discovery hub."),
        ("Papers with Code Latest", "https://paperswithcode.com/latest", "article_hub", "Public paper and code trend hub."),
        ("OpenReview recent activity", "https://openreview.net/", "conference_feed", "Public conference and workshop paper review platform."),
    )

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:  # noqa: ARG002
        keys = _keywords(request.topic, limit=8)
        candidates: list[DiscoveryCandidate] = []
        for title, url, source_type, reliability in self._SOURCES:
            title_lower = title.lower()
            if keys and not any(key in title_lower or key in reliability.lower() for key in keys):
                # Keep broad academic sources for sparse Korean or niche prompts,
                # but do not flood unrelated lab blogs for every request.
                if source_type == "research_lab_blog" and len(candidates) >= 2:
                    continue
            candidates.append(
                DiscoveryCandidate(
                    url=url,
                    title=title,
                    source_type=source_type,
                    reliability_note=f"정적 고신뢰 공개 출처 포트폴리오: {reliability}",
                    cadence_note="공개 목록/블로그/허브 형태로 반복 갱신 확인이 가능한 출처입니다.",
                    topic_fit=f"요청 주제 `{clean_text(request.topic, limit=120)}`의 수집면 확장을 위해 운영자 검토가 필요한 후보입니다.",
                    collection_hint="review_static_public_source_surface",
                    provider=self.name,
                )
            )
        return DiscoveryProviderResult(provider=self.name, reviewed_count=len(self._SOURCES), candidates=_dedupe_candidates(candidates))


def default_providers() -> list[DiscoveryProvider]:
    provider_names = [name.strip() for name in os.environ.get("JIPHYEONJEON_TRAVELER_DISCOVERY_PROVIDERS", "arxiv,semantic_scholar,static").split(",")]
    providers: list[DiscoveryProvider] = []
    for name in provider_names:
        if name in {"arxiv", "arxiv-api"}:
            providers.append(ArxivDiscoveryProvider())
        elif name in {"semantic_scholar", "semantic-scholar", "semantic-scholar-graph"}:
            providers.append(SemanticScholarDiscoveryProvider())
        elif name in {"static", "static-technical-sources"}:
            providers.append(StaticTechnicalSourceProvider())
    return providers or [StaticTechnicalSourceProvider()]


def _status_payload(summary: DiscoveryRunSummary, *, provider_results: list[DiscoveryProviderResult]) -> dict[str, Any]:
    return {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "requests_seen": summary.requests_seen,
        "requests_processed": summary.requests_processed,
        "providers_used": summary.providers_used,
        "reviewed_count": summary.reviewed_count,
        "accepted_count": summary.accepted_count,
        "duplicate_count": summary.duplicate_count,
        "rejected_count": summary.rejected_count,
        "error_count": summary.error_count,
        "candidate_queue_path": summary.candidate_queue_path,
        "provider_results": [
            {
                "provider": result.provider,
                "reviewed_count": result.reviewed_count,
                "candidate_count": len(result.candidates),
                "rejected_count": len(result.rejected),
                "rejected_samples": result.rejected[:10],
                "error": result.error,
            }
            for result in provider_results
        ],
    }


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


async def discover_sources(
    *,
    research_queue_path: Path | None = None,
    default_candidate_queue_path: Path | None = None,
    providers: list[DiscoveryProvider] | None = None,
    max_candidates: int | None = None,
    status_path: Path | None = None,
    dry_run: bool = False,
    timeout_sec: float = 20.0,
) -> DiscoveryRunSummary:
    research_queue = (research_queue_path or default_research_queue_path()).expanduser()
    candidate_queue = (default_candidate_queue_path or default_source_queue_path()).expanduser()
    requests = load_pending_requests(research_queue, default_candidate_queue=candidate_queue)
    max_requests = int(os.environ.get("JIPHYEONJEON_TRAVELER_DISCOVERY_MAX_REQUESTS", "3"))
    if max_requests > 0:
        requests = requests[:max_requests]
    selected_providers = providers or default_providers()
    max_to_record = max_candidates if max_candidates is not None else int(os.environ.get("JIPHYEONJEON_TRAVELER_DISCOVERY_MAX_CANDIDATES", "12"))
    max_to_record = max(0, max_to_record)

    accepted = 0
    duplicate = 0
    rejected = 0
    errors = 0
    reviewed = 0
    provider_results: list[DiscoveryProviderResult] = []
    processed_ids: set[str] = set()

    async with httpx.AsyncClient(timeout=timeout_sec, headers={"User-Agent": "AutoResearchClaw-Traveler/0.1"}) as client:
        for request in requests:
            processed_ids.add(request.request_id)
            request_results: list[DiscoveryProviderResult] = []
            for provider in selected_providers:
                try:
                    result = await provider.discover(request, client=client)
                except Exception as exc:  # provider failures must not block status emission
                    result = DiscoveryProviderResult(
                        provider=provider.name,
                        reviewed_count=0,
                        error=str(exc),
                        rejected=["provider raised an unexpected error"],
                    )
                request_results.append(result)
                provider_results.append(result)
                reviewed += result.reviewed_count
                rejected += len(result.rejected)
                if result.error:
                    errors += 1
                    LOG.warning("traveler discovery provider failed request=%s provider=%s error=%s", request.request_id, result.provider, result.error)

            request_reviewed = sum(result.reviewed_count for result in request_results)
            if request_reviewed < request.min_sources_to_review:
                rejected += sum(len(result.candidates) for result in request_results)
                LOG.warning(
                    "traveler discovery below minimum review threshold request=%s reviewed=%s required=%s",
                    request.request_id,
                    request_reviewed,
                    request.min_sources_to_review,
                )
                continue

            for candidate in _dedupe_candidates(candidate for result in request_results for candidate in result.candidates):
                if accepted >= max_to_record:
                    break
                source = TravelerSourceInput(
                    url=candidate.url,
                    title=candidate.title,
                    source_type=candidate.source_type,
                    reliability_note=candidate.reliability_note,
                    cadence_note=candidate.cadence_note,
                    topic_fit=f"{candidate.topic_fit} (request={request.request_id}, provider={candidate.provider})",
                    collection_hint=candidate.collection_hint,
                    access_constraints=candidate.access_constraints,
                    next_action=candidate.next_action,
                )
                try:
                    build_source_candidate_record(source, queue_path=request.candidate_queue_path)
                    record: TravelerRecordResult | None = None
                    if not dry_run:
                        record = record_source_candidate(source, queue_path=request.candidate_queue_path)
                except ValueError as exc:
                    rejected += 1
                    LOG.info("traveler discovery rejected url=%s reason=%s", candidate.url, exc)
                    continue
                if dry_run:
                    accepted += 1
                elif record is not None and record.duplicate:
                    duplicate += 1
                else:
                    accepted += 1
            if accepted >= max_to_record:
                break

    summary = DiscoveryRunSummary(
        requests_seen=len(requests),
        requests_processed=len(processed_ids),
        providers_used=[provider.name for provider in selected_providers],
        reviewed_count=reviewed,
        accepted_count=accepted,
        duplicate_count=duplicate,
        rejected_count=rejected,
        error_count=errors,
        candidate_queue_path=str(candidate_queue),
        status_path=str(status_path) if status_path else None,
    )
    if status_path is not None:
        _write_status(status_path.expanduser(), _status_payload(summary, provider_results=provider_results))
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-traveler-discover-sources",
        description="Discover public Traveler source candidates and write them to the pending review queue.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Run providers and print the summary without appending candidates.")
    parser.add_argument("--research-queue", type=Path, default=None, help="Override Traveler research-request JSONL path.")
    parser.add_argument("--source-queue", type=Path, default=None, help="Override default Traveler source-candidate JSONL path.")
    parser.add_argument("--max-candidates", type=int, default=None, help="Maximum new candidates to append in this run.")
    parser.add_argument("--status-path", type=Path, default=None, help="Status JSON path. Defaults to env or workspace state path.")
    parser.add_argument("--timeout-sec", type=float, default=20.0, help="HTTP timeout per provider request.")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = _build_parser().parse_args(argv)
    status_path = args.status_path or Path(os.environ.get("JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH", str(DEFAULT_DISCOVERY_STATE_PATH))).expanduser()
    try:
        summary = asyncio.run(
            discover_sources(
                research_queue_path=args.research_queue,
                default_candidate_queue_path=args.source_queue,
                max_candidates=args.max_candidates,
                status_path=status_path,
                dry_run=args.dry_run,
                timeout_sec=args.timeout_sec,
            )
        )
    except Exception as exc:
        print(f"traveler source discovery error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
