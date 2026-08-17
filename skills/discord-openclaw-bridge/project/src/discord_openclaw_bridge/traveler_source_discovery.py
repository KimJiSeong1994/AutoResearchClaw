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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .miner import clean_text, sanitize_url
from .traveler import (
    TravelerRecordResult,
    TravelerSourceInput,
    build_source_candidate_record,
    default_research_queue_path,
    default_source_queue_path,
    record_source_candidate,
)
from .traveler_evidence import (
    append_evidence,
    collect_evidence_for_url,
    default_evidence_path,
    load_scoring,
    read_scoring_config,
)

LOG = logging.getLogger(__name__)
def _default_workspace() -> Path:
    return Path(
        os.environ.get("HERMES_WORKSPACE")
        or os.environ.get("OPENCLAW_WORKSPACE")
        or str(Path.home() / ".hermes" / "workspace")
    ).expanduser()


DEFAULT_DISCOVERY_STATE_PATH = _default_workspace() / "state" / "traveler-source-discovery-last-status.json"


def default_discovery_status_path() -> Path:
    return _default_workspace() / "state" / "traveler-source-discovery-last-status.json"
ARXIV_API_URL = "https://export.arxiv.org/api/query"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
# alphaXiv's root page is the public Hot feed and exposes a schema.org
# ``Trending Papers`` ItemList. robots.txt disallows query-string crawling, so
# the provider deliberately does not request ``/?sort=Hot``.
ALPHAXIV_HOT_URL = "https://www.alphaxiv.org/"
ALPHAXIV_HOT_FEED_URL = "https://api.alphaxiv.org/papers/v3/feed"
ALPHAXIV_HOT_DEFAULT_POOL_SIZE = 100
REQUEST_STATUS_PENDING = "pending_deep_research"
REQUEST_STATUS_COMPLETED = "completed_source_discovery"
REQUEST_STATUS_COMPLETED_EMPTY = "completed_no_candidates"
REQUEST_STATUS_FAILED = "failed_source_discovery"
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}
MIN_NETWORK_FAILURE_FALLBACK_REVIEWED = 10


DEFAULT_STATIC_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("arXiv cs.AI recent submissions", "https://arxiv.org/list/cs.AI/recent", "conference_feed", "arXiv official recent list for AI papers."),
    ("arXiv cs.CL recent submissions", "https://arxiv.org/list/cs.CL/recent", "conference_feed", "arXiv official recent list for computational linguistics papers."),
    ("OpenAI Research", "https://openai.com/research/", "research_lab_blog", "Official public research announcements and papers."),
    ("Google Research Blog", "https://research.google/blog/", "research_lab_blog", "Official public AI and systems research blog."),
    ("Anthropic Research", "https://www.anthropic.com/research", "research_lab_blog", "Official public research publication surface for model safety and AI systems."),
    ("Hugging Face Papers", "https://huggingface.co/papers", "article_hub", "Public daily paper discovery hub."),
    ("Papers with Code Latest", "https://paperswithcode.com/latest", "article_hub", "Public paper and code trend hub."),
    ("OpenReview recent activity", "https://openreview.net/", "conference_feed", "Public conference and workshop paper review platform."),
    ("Microsoft Research Blog", "https://www.microsoft.com/en-us/research/blog/", "research_lab_blog", "Official public research blog with recurring AI and systems posts."),
    ("Meta AI Research", "https://ai.meta.com/research/", "research_lab_blog", "Official public AI research publication surface."),
    ("T2-RAGBench", "https://aclanthology.org/2026.eacl-long.8/", "paper_page", "ACL Anthology paper page for text-and-table retrieval augmented generation evaluation benchmark."),
    ("URAG benchmark", "https://arxiv.org/abs/2603.19281", "paper_page", "arXiv abstract page for uncertainty quantification in retrieval augmented generation evaluation."),
    ("IBM RAG hyper-parameter optimization", "https://research.ibm.com/publications/an-analysis-of-hyper-parameter-optimization-methods-for-retrieval-augmented-generation", "paper_page", "IBM Research publication page for retrieval augmented generation hyper-parameter optimization analysis."),
    ("DCTR graph RAG", "https://ojs.aaai.org/index.php/AAAI/article/view/40265", "paper_page", "AAAI paper page for knowledge graph and retrieval augmented generation subgraph optimization."),
    ("Agent-as-a-Graph", "https://arxiv.org/abs/2511.18194", "paper_page", "arXiv abstract page for knowledge graph based LLM agent and tool retrieval."),
)


def load_static_sources() -> tuple[tuple[str, str, str, str], ...]:
    """Curated portfolio from config, falling back to the committed defaults.

    A row must be four non-empty strings with an https URL; anything else is
    dropped rather than trusted, and an empty result falls back wholesale so a
    bad edit cannot silently leave the traveler with no static fallback.
    """
    rows = read_scoring_config().get("static_sources")
    if not isinstance(rows, list):
        return DEFAULT_STATIC_SOURCES
    parsed = [
        (row[0], row[1], row[2], row[3])
        for row in rows
        if isinstance(row, list)
        and len(row) == 4
        and all(isinstance(cell, str) and cell.strip() for cell in row)
        and row[1].startswith("https://")
    ]
    return tuple(parsed) or DEFAULT_STATIC_SOURCES


@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    topic: str
    scope: str
    min_sources_to_review: int
    candidate_queue_path: Path
    max_candidates: int | None = None
    discovery_mode: str = "requested"
    scout_topic_id: str = ""
    scout_priority: str = ""
    topic_source: str = ""
    paperwiki_interest_slug: str = ""


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
    error_kind: str | None = None


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
    evidence_path: str | None = None
    evidence_count: int = 0
    evidence_rejected_count: int = 0
    deep_research_enabled: bool = False


@dataclass(frozen=True)
class AlphaXivHotPaper:
    position: int
    url: str
    title: str
    description: str
    published_at: str = ""
    views: int = 0
    likes: int = 0


class DiscoveryProvider(Protocol):
    name: str

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        ...



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


def _is_test_research_request(row: dict[str, Any]) -> bool:
    topic = clean_text(row.get("topic"), limit=300).lower()
    note = clean_text(row.get("requester_note"), limit=300).lower()
    return (
        topic.startswith("live test")
        or "safe to ignore" in note
        or "formatting live test" in note
        or "live content test" in note
        or "연결 검증" in topic
        or "표시 검증" in topic
    )


def _request_from_row(row: dict[str, Any], *, default_candidate_queue: Path) -> ResearchRequest | None:
    if row.get("status") != REQUEST_STATUS_PENDING:
        return None
    if _is_test_research_request(row):
        LOG.info("skipping traveler test research request request_id=%s", row.get("request_id"))
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
    try:
        max_candidates = int(row["max_candidates"]) if row.get("max_candidates") is not None else None
    except (TypeError, ValueError):
        max_candidates = None
    if max_candidates is not None:
        max_candidates = max(1, min(max_candidates, 50))
    candidate_path = _safe_candidate_queue_path(row.get("candidate_queue_path"), default_candidate_queue=default_candidate_queue)
    return ResearchRequest(
        request_id=request_id,
        topic=topic,
        scope=scope,
        min_sources_to_review=min_sources,
        candidate_queue_path=candidate_path,
        max_candidates=max_candidates,
        discovery_mode=clean_text(row.get("discovery_mode") or "requested", limit=80),
        scout_topic_id=clean_text(row.get("scout_topic_id"), limit=120),
        scout_priority=clean_text(row.get("scout_priority"), limit=40),
        topic_source=clean_text(row.get("topic_source"), limit=80),
        paperwiki_interest_slug=clean_text(row.get("paperwiki_interest_slug"), limit=120),
    )


def load_pending_requests(path: Path, *, default_candidate_queue: Path) -> list[ResearchRequest]:
    return [
        request
        for row in _read_jsonl_rows(path)
        if (request := _request_from_row(row, default_candidate_queue=default_candidate_queue)) is not None
    ]


def _write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _mark_processed_requests(path: Path, updates: dict[str, dict[str, Any]]) -> None:
    if not updates or not path.exists():
        return
    rows = _read_jsonl_rows(path)
    changed = False
    completed_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for row in rows:
        request_id = clean_text(row.get("request_id"), limit=120)
        update = updates.get(request_id)
        if not update or row.get("status") != REQUEST_STATUS_PENDING:
            continue
        row.update(update)
        row["completed_at"] = completed_at
        changed = True
    if changed:
        _write_jsonl_rows(path, rows)


def _keywords(topic: str, *, limit: int = 8) -> list[str]:
    words: list[str] = []
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}|[가-힣]{2,}", topic):
        word = raw.lower()
        if word in {"and", "the", "for", "with", "from", "into", "using", "source", "sources"}:
            continue
        if word not in words:
            words.append(word)
    return words[:limit]


SEARCH_QUERY_RE = re.compile(r"(?i)(^|&)(q|query|search_query|search|keywords?)=")
SEARCH_PATH_RE = re.compile(r"(?i)/(search|find|results?)(/|$|\?)")


def is_search_surface(url: str) -> bool:
    """True when a URL is a query snapshot rather than a stable source.

    Review rejected every search URL the traveler ever proposed: a result page
    changes with ranking and cannot be re-fetched deterministically, so it is
    not something to collect from. Paper pages, listing feeds, and blogs are
    stable and must keep passing.
    """
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if SEARCH_QUERY_RE.search(parts.query or ""):
        return True
    return bool(SEARCH_PATH_RE.search(parts.path or ""))


def _topic_fit(topic: str, title: str) -> str:
    keys = _keywords(topic, limit=5)
    matched = [key for key in keys if key.lower() in title.lower()]
    if matched:
        return f"요청 주제와 겹치는 공개 메타데이터 키워드: {', '.join(matched)}."
    return f"요청 주제 `{clean_text(topic, limit=120)}`의 후보 출처로 추가 샘플 검토가 필요합니다."


class _JsonLdScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self._buffer: list[str] = []
        self.payloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script" and dict(attrs).get("type", "").lower() == "application/ld+json":
            self._capturing = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing:
            self.payloads.append("".join(self._buffer))
            self._capturing = False
            self._buffer = []


def parse_alphaxiv_hot_papers(html: str) -> list[AlphaXivHotPaper]:
    """Extract alphaXiv's public Hot feed from schema.org JSON-LD."""
    parser = _JsonLdScriptParser()
    parser.feed(html)
    papers: list[AlphaXivHotPaper] = []
    seen: set[str] = set()
    for raw in parser.payloads:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        graph = payload.get("@graph", []) if isinstance(payload, dict) else []
        if not isinstance(graph, list):
            continue
        for node in graph:
            if not isinstance(node, dict) or node.get("@type") != "ItemList":
                continue
            if str(node.get("name") or "").strip().lower() not in {"trending papers", "hot papers"}:
                continue
            elements = node.get("itemListElement", [])
            if not isinstance(elements, list):
                continue
            for element in elements:
                if not isinstance(element, dict):
                    continue
                item = element.get("item") if isinstance(element.get("item"), dict) else {}
                safe_url = sanitize_url(str(item.get("url") or ""))
                try:
                    parts = urlsplit(safe_url)
                except ValueError:
                    continue
                if parts.netloc.lower() not in {"alphaxiv.org", "www.alphaxiv.org"} or not parts.path.startswith("/abs/"):
                    continue
                title = clean_text(item.get("headline"), limit=240)
                if not title or safe_url in seen:
                    continue
                seen.add(safe_url)
                interactions = item.get("interactionStatistic", [])
                views = 0
                likes = 0
                if isinstance(interactions, list):
                    for interaction in interactions:
                        if not isinstance(interaction, dict):
                            continue
                        interaction_type = interaction.get("interactionType")
                        kind = str(interaction_type.get("@type") or "") if isinstance(interaction_type, dict) else ""
                        try:
                            count = max(0, int(interaction.get("userInteractionCount") or 0))
                        except (TypeError, ValueError):
                            count = 0
                        if kind == "ViewAction":
                            views = count
                        elif kind == "LikeAction":
                            likes = count
                try:
                    position = max(1, int(element.get("position") or len(papers) + 1))
                except (TypeError, ValueError):
                    position = len(papers) + 1
                papers.append(
                    AlphaXivHotPaper(
                        position=position,
                        url=safe_url,
                        title=title,
                        description=clean_text(item.get("description"), limit=2400),
                        published_at=clean_text(item.get("datePublished"), limit=80),
                        views=views,
                        likes=likes,
                    )
                )
    return sorted(papers, key=lambda paper: paper.position)


def parse_alphaxiv_hot_feed(payload: Any, *, page_num: int = 0, page_size: int = ALPHAXIV_HOT_DEFAULT_POOL_SIZE) -> list[AlphaXivHotPaper]:
    """Normalize one public alphaXiv Hot feed API page."""
    rows = payload.get("papers", []) if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    papers: list[AlphaXivHotPaper] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        paper_id = clean_text(row.get("universal_paper_id"), limit=120)
        if not paper_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", paper_id) or ".." in paper_id:
            continue
        safe_url = sanitize_url(f"https://www.alphaxiv.org/abs/{paper_id}")
        title = clean_text(row.get("title"), limit=240)
        if not safe_url or not title:
            continue
        summary = row.get("paper_summary") if isinstance(row.get("paper_summary"), dict) else {}
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        visits = metrics.get("visits_count") if isinstance(metrics.get("visits_count"), dict) else {}
        try:
            views = max(0, int(visits.get("last_7_days") or visits.get("all") or 0))
        except (TypeError, ValueError):
            views = 0
        try:
            likes = max(0, int(metrics.get("public_total_votes") or metrics.get("total_votes") or 0))
        except (TypeError, ValueError):
            likes = 0
        papers.append(
            AlphaXivHotPaper(
                position=(max(0, page_num) * max(1, page_size)) + index + 1,
                url=safe_url,
                title=title,
                description=clean_text(row.get("abstract") or summary.get("summary"), limit=2400),
                published_at=clean_text(row.get("publication_date") or row.get("first_publication_date"), limit=80),
                views=views,
                likes=likes,
            )
        )
    return papers


_ALPHAXIV_INTEREST_STOPWORDS = {
    "public",
    "research",
    "engineering",
    "source",
    "sources",
    "paper",
    "papers",
    "technical",
    "high",
    "multi",
    "trust",
    "recurring",
}


def _alphaxiv_interest_tokens(request: ResearchRequest) -> list[str]:
    tokens: list[str] = []
    for raw in _keywords(f"{request.topic} {request.scope}", limit=32):
        token = raw[:-1] if raw.endswith("s") and len(raw) > 5 else raw
        if token in _ALPHAXIV_INTEREST_STOPWORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def _alphaxiv_text_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+-]{1,}|[가-힣]{2,}", value)}


def _alphaxiv_token_matches(token: str, words: set[str]) -> bool:
    if token in words:
        return True
    stem = token[:-1] if token.endswith("s") and len(token) > 5 else token
    return len(stem) >= 5 and any(word.startswith(stem) for word in words)


def _rank_alphaxiv_hot_papers(request: ResearchRequest, papers: list[AlphaXivHotPaper]) -> list[tuple[AlphaXivHotPaper, list[str], float]]:
    interest_tokens = _alphaxiv_interest_tokens(request)
    ranked: list[tuple[AlphaXivHotPaper, list[str], float]] = []
    for paper in papers:
        title_tokens = _alphaxiv_text_tokens(paper.title)
        description_tokens = _alphaxiv_text_tokens(paper.description)
        title_matches = [token for token in interest_tokens if _alphaxiv_token_matches(token, title_tokens)]
        description_matches = [
            token
            for token in interest_tokens
            if token not in title_matches and _alphaxiv_token_matches(token, description_tokens)
        ]
        matched = title_matches + description_matches
        if not matched:
            continue
        coverage = len(matched) / max(1, len(interest_tokens))
        hot_tiebreaker = 1.0 / max(1, paper.position)
        score = (4.0 * len(title_matches)) + (1.5 * len(description_matches)) + coverage + (0.1 * hot_tiebreaker)
        ranked.append((paper, matched, score))
    return sorted(ranked, key=lambda row: (-row[2], row[0].position, row[0].url))


def _dedupe_candidates(candidates: Iterable[DiscoveryCandidate]) -> list[DiscoveryCandidate]:
    """Drop duplicates and search surfaces.

    Every provider funnels through here, so the search-surface guard lives at
    this one point rather than in each provider.
    """
    seen: set[str] = set()
    deduped: list[DiscoveryCandidate] = []
    for candidate in candidates:
        safe_url = sanitize_url(candidate.url)
        if is_search_surface(safe_url or candidate.url):
            LOG.info("traveler discovery dropped search surface url=%s provider=%s", candidate.url, candidate.provider)
            continue
        key = safe_url or f"invalid:{candidate.url}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


async def _get_with_backoff(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str],
    provider: str,
    attempts: int | None = None,
    timeout: float | None = None,
) -> httpx.Response:
    max_attempts = attempts if attempts is not None else int(os.environ.get("JIPHYEONJEON_TRAVELER_PROVIDER_RETRY_ATTEMPTS", "2"))
    max_attempts = max(1, min(max_attempts, 4))
    last_exc: httpx.HTTPError | None = None
    for attempt in range(max_attempts):
        try:
            request_kwargs: dict[str, Any] = {"params": params}
            if timeout is not None:
                request_kwargs["timeout"] = timeout
            response = await client.get(url, **request_kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            status_code = exc.response.status_code
            if status_code not in RETRYABLE_HTTP_STATUS or attempt >= max_attempts - 1:
                raise
            retry_after = exc.response.headers.get("Retry-After", "")
            try:
                delay = float(retry_after) if retry_after else 0.5 * (2**attempt)
            except ValueError:
                delay = 0.5 * (2**attempt)
            delay = min(5.0, max(0.1, delay))
            LOG.info("traveler provider retry provider=%s status=%s attempt=%s delay=%s", provider, status_code, attempt + 1, delay)
            await asyncio.sleep(delay)
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt >= max_attempts - 1:
                raise
            delay = min(5.0, 0.5 * (2**attempt))
            LOG.info("traveler provider retry provider=%s error=%s attempt=%s delay=%s", provider, exc, attempt + 1, delay)
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _provider_error_kind(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            return "rate_limited"
        if status_code in RETRYABLE_HTTP_STATUS:
            return "retryable_http"
        return "http_error"
    return "network"


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
            response = await _get_with_backoff(client, ARXIV_API_URL, params=params, provider=self.name)
        except httpx.HTTPError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=str(exc), error_kind=_provider_error_kind(exc), rejected=["arXiv API request failed"])

        reviewed = 0
        candidates: list[DiscoveryCandidate] = []
        rejected: list[str] = []
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=f"arXiv XML parse failed: {exc}", error_kind="parse")
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


class AlphaXivHotDiscoveryProvider:
    """Recommend alphaXiv Hot papers against the request's KG-exported scope."""

    name = "alphaxiv-hot"

    def __init__(self) -> None:
        self._papers: list[AlphaXivHotPaper] | None = None
        self._load_error: tuple[str, str] | None = None
        self._load_warning: str | None = None

    async def _load(self, client: httpx.AsyncClient) -> list[AlphaXivHotPaper]:
        if self._papers is not None:
            return self._papers
        if self._load_error is not None:
            return []
        try:
            raw_timeout = float(os.environ.get("JIPHYEONJEON_TRAVELER_ALPHAXIV_TIMEOUT_SEC", "30"))
        except ValueError:
            raw_timeout = 30.0
        timeout = max(5.0, min(raw_timeout, 60.0))
        try:
            raw_pool_size = int(
                os.environ.get(
                    "JIPHYEONJEON_TRAVELER_ALPHAXIV_HOT_POOL_SIZE",
                    str(ALPHAXIV_HOT_DEFAULT_POOL_SIZE),
                )
            )
        except ValueError:
            raw_pool_size = ALPHAXIV_HOT_DEFAULT_POOL_SIZE
        pool_size = max(20, min(raw_pool_size, ALPHAXIV_HOT_DEFAULT_POOL_SIZE))
        papers: list[AlphaXivHotPaper] = []
        params = {
            "pageNum": "0",
            "sort": "Hot",
            "pageSize": str(pool_size),
            "interval": "7 Days",
            "topics": "[]",
        }
        try:
            response = await _get_with_backoff(
                client,
                ALPHAXIV_HOT_FEED_URL,
                params=params,
                provider=self.name,
                timeout=timeout,
            )
            page_payload = response.json()
            papers = parse_alphaxiv_hot_feed(page_payload, page_size=pool_size)
        except (httpx.HTTPError, ValueError) as exc:
            self._load_warning = f"alphaXiv Hot 100 feed request failed: {exc}"
        if not papers:
            # The public root JSON-LD remains a robots-safe 20-paper fallback
            # when the feed API is temporarily unavailable.
            try:
                response = await _get_with_backoff(
                    client,
                    ALPHAXIV_HOT_URL,
                    params={},
                    provider=self.name,
                    timeout=timeout,
                )
            except httpx.HTTPError as exc:
                self._load_error = (_provider_error_kind(exc), str(exc))
                return []
            papers = parse_alphaxiv_hot_papers(response.text)
        if not papers:
            self._load_error = ("parse", "alphaXiv Hot feed and Trending Papers JSON-LD were empty")
            return []
        deduped = {paper.url: paper for paper in papers}
        self._papers = sorted(deduped.values(), key=lambda paper: paper.position)[:pool_size]
        if len(self._papers) < pool_size and self._load_warning is None:
            self._load_warning = f"alphaXiv Hot pool returned {len(self._papers)} of requested {pool_size} papers"
        return self._papers

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        papers = await self._load(client)
        if self._load_error is not None:
            error_kind, error = self._load_error
            return DiscoveryProviderResult(
                provider=self.name,
                reviewed_count=0,
                error=error,
                error_kind=error_kind,
                rejected=["alphaXiv Hot feed request failed" if error_kind != "parse" else "alphaXiv Hot feed parse failed"],
            )
        ranked = _rank_alphaxiv_hot_papers(request, papers)
        try:
            configured_limit = int(os.environ.get("JIPHYEONJEON_TRAVELER_ALPHAXIV_MAX_CANDIDATES", "6"))
        except ValueError:
            configured_limit = 6
        limit = max(1, min(configured_limit, 12))
        candidates: list[DiscoveryCandidate] = []
        kg_basis = request.topic_source in {"interest-note", "paperwiki-kg"} or bool(request.paperwiki_interest_slug)
        basis_label = "PaperWiki KG 공개 관심도" if kg_basis else "Traveler 관심 주제"
        for paper, matched, _score in ranked[:limit]:
            candidates.append(
                DiscoveryCandidate(
                    url=paper.url,
                    title=paper.title,
                    source_type="paper_page",
                    reliability_note=(
                        f"alphaXiv 공개 Hot 목록 {paper.position}위 논문입니다. 인기 신호는 품질 승인이 아니며 Claw 검토가 필요합니다"
                        f" (views={paper.views}, likes={paper.likes})."
                    ),
                    cadence_note="alphaXiv 루트의 공개 Trending Papers JSON-LD에서 실행당 한 번 수집한 Hot 후보입니다.",
                    topic_fit=f"{basis_label}와 겹치는 키워드: {', '.join(matched[:8])}.",
                    collection_hint="review_alphaxiv_hot_paper",
                    access_constraints="public_json_ld_metadata",
                    next_action="review_hot_paper_for_miner_collection",
                    provider=self.name,
                )
            )
        matched_urls = {paper.url for paper, _matched, _score in ranked}
        rejected = [f"{paper.title}: no KG/topic keyword overlap" for paper in papers if paper.url not in matched_urls]
        return DiscoveryProviderResult(
            provider=self.name,
            reviewed_count=len(papers),
            candidates=_dedupe_candidates(candidates),
            rejected=rejected,
            error=self._load_warning,
            error_kind="partial_hot_feed" if self._load_warning else None,
        )


class SemanticScholarDiscoveryProvider:
    name = "semantic-scholar-graph"

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        params = {
            "query": request.topic,
            "limit": str(min(max(request.min_sources_to_review, 10), 50)),
            "fields": "paperId,title,venue,year,externalIds,openAccessPdf,url",
        }
        try:
            response = await _get_with_backoff(client, SEMANTIC_SCHOLAR_SEARCH_URL, params=params, provider=self.name)
        except httpx.HTTPError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=str(exc), error_kind=_provider_error_kind(exc), rejected=["Semantic Scholar API request failed"])
        try:
            payload = response.json()
        except ValueError as exc:
            return DiscoveryProviderResult(provider=self.name, reviewed_count=0, error=f"Semantic Scholar JSON parse failed: {exc}", error_kind="parse")
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
            # Propose the paper itself, never a search URL for it. A query
            # snapshot is not a recollectable source: its contents change with
            # ranking, it cannot be re-fetched deterministically, and every one
            # of these this provider produced was rejected on review. The
            # identifiers below address a stable page.
            paper_id = clean_text(item.get("paperId"), limit=80)
            doi = clean_text(external.get("DOI"), limit=120)
            if arxiv_id:
                url = f"https://arxiv.org/abs/{arxiv_id}"
                source_type = "paper_page"
                title_text = title or f"arXiv {arxiv_id}"
                cadence = "arXiv 초록 페이지는 식별자로 고정되어 언제든 동일한 공개 원문을 다시 확인할 수 있습니다."
            elif doi:
                url = f"https://doi.org/{doi}"
                source_type = "paper_page"
                title_text = title or f"DOI {doi}"
                cadence = "DOI는 영구 식별자로 공개 원문 페이지를 안정적으로 가리킵니다."
            elif paper_id:
                url = f"https://www.semanticscholar.org/paper/{paper_id}"
                source_type = "paper_page"
                title_text = title or f"Semantic Scholar paper {paper_id}"
                cadence = "Semantic Scholar 논문 페이지는 paperId로 고정된 공개 메타데이터 면입니다."
            else:
                rejected.append(f"{title or 'untitled'}: no stable public paper identifier")
                continue
            if venue:
                title_text = f"{title_text} ({venue})"
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
    # Portfolio lives in runtime/traveler-scoring.json so operators can add a
    # source without a code deploy; load_static_sources falls back to the
    # committed defaults when the file is missing or malformed.
    _SOURCES = load_static_sources()

    def _candidate_rows(self, request: ResearchRequest) -> list[tuple[str, str, str, str]]:
        keys = _keywords(request.topic, limit=8)
        candidates: list[tuple[str, str, str, str]] = []
        for title, url, source_type, reliability in self._SOURCES:
            title_lower = title.lower()
            reliability_lower = reliability.lower()
            has_topic_match = any(key in title_lower or key in reliability_lower for key in keys)
            if source_type == "paper_page" and (not keys or not has_topic_match):
                continue
            # Keep broad academic/infrastructure hubs for sparse prompts, but
            # do not flood unrelated lab blogs or paper pages.
            if keys and not has_topic_match and source_type == "research_lab_blog" and len(candidates) >= 2:
                continue
            candidates.append((title, url, source_type, reliability))
        return candidates

    async def discover(self, request: ResearchRequest, *, client: httpx.AsyncClient) -> DiscoveryProviderResult:
        candidates: list[DiscoveryCandidate] = []
        rows = self._candidate_rows(request)
        for title, url, source_type, reliability in rows:
            next_action = "review_paper_lead_for_recurring_source" if source_type == "paper_page" else "review_for_miner_seed"
            collection_hint = "review_paper_page_for_feed_or_author_source" if source_type == "paper_page" else "review_static_public_source_surface"
            candidates.append(
                DiscoveryCandidate(
                    url=url,
                    title=title,
                    source_type=source_type,
                    reliability_note=f"정적 고신뢰 공개 출처 포트폴리오: {reliability}",
                    cadence_note="공개 목록/블로그/허브 형태로 반복 갱신 확인이 가능한 출처입니다.",
                    topic_fit=f"요청 주제 `{clean_text(request.topic, limit=120)}`의 수집면 확장을 위해 운영자 검토가 필요한 후보입니다.",
                    collection_hint=collection_hint,
                    next_action=next_action,
                    provider=self.name,
                )
            )
        return DiscoveryProviderResult(provider=self.name, reviewed_count=len({sanitize_url(url) or url for _, url, _, _ in rows}), candidates=_dedupe_candidates(candidates))


def default_providers() -> list[DiscoveryProvider]:
    provider_names = [
        name.strip()
        for name in os.environ.get(
            "JIPHYEONJEON_TRAVELER_DISCOVERY_PROVIDERS",
            "arxiv,alphaxiv_hot,semantic_scholar,static",
        ).split(",")
    ]
    providers: list[DiscoveryProvider] = []
    for name in provider_names:
        if name in {"arxiv", "arxiv-api"}:
            providers.append(ArxivDiscoveryProvider())
        elif name in {"alphaxiv", "alphaxiv_hot", "alphaxiv-hot"}:
            providers.append(AlphaXivHotDiscoveryProvider())
        elif name in {"semantic_scholar", "semantic-scholar", "semantic-scholar-graph"}:
            providers.append(SemanticScholarDiscoveryProvider())
        elif name in {"static", "static-technical-sources"}:
            providers.append(StaticTechnicalSourceProvider())
    return providers or [StaticTechnicalSourceProvider()]


def _status_payload(summary: DiscoveryRunSummary, *, provider_results: list[DiscoveryProviderResult]) -> dict[str, Any]:
    return {
        "run_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "requests_seen": summary.requests_seen,
        "requests_processed": summary.requests_processed,
        "providers_used": summary.providers_used,
        "reviewed_count": summary.reviewed_count,
        "accepted_count": summary.accepted_count,
        "duplicate_count": summary.duplicate_count,
        "rejected_count": summary.rejected_count,
        "error_count": summary.error_count,
        "candidate_queue_path": summary.candidate_queue_path,
        "evidence_path": summary.evidence_path,
        "evidence_count": summary.evidence_count,
        "evidence_rejected_count": summary.evidence_rejected_count,
        "deep_research_enabled": summary.deep_research_enabled,
        "provider_results": [
            {
                "provider": result.provider,
                "reviewed_count": result.reviewed_count,
                "candidate_count": len(result.candidates),
                "rejected_count": len(result.rejected),
                "rejected_samples": result.rejected[:10],
                "error": result.error,
                "error_kind": result.error_kind,
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
    deep_research: bool | None = None,
    evidence_path: Path | None = None,
    evidence_fetcher: Callable[[str], Any] | None = None,
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
    deep_enabled = (os.environ.get("JIPHYEONJEON_TRAVELER_DEEP_RESEARCH", "1") != "0") if deep_research is None else deep_research
    evidence_queue = (evidence_path or default_evidence_path()).expanduser()

    accepted = 0
    duplicate = 0
    rejected = 0
    errors = 0
    reviewed = 0
    evidence_count = 0
    evidence_rejected_count = 0
    provider_results: list[DiscoveryProviderResult] = []
    processed_ids: set[str] = set()
    request_updates: dict[str, dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=timeout_sec, headers={"User-Agent": "AutoResearchClaw-Traveler/0.1"}) as client:
        for request in requests:
            processed_ids.add(request.request_id)
            request_results: list[DiscoveryProviderResult] = []
            request_accepted_before = accepted
            request_duplicate_before = duplicate
            request_errors_before = errors
            request_rejected_before = rejected
            for provider in selected_providers:
                try:
                    result = await provider.discover(request, client=client)
                except Exception as exc:  # noqa: BLE001 - provider failures must not block status emission.
                    result = DiscoveryProviderResult(
                        provider=provider.name,
                        reviewed_count=0,
                        error=str(exc),
                        error_kind="unexpected",
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
            provider_error_count = sum(1 for result in request_results if result.error)
            retryable_error_kinds = {"rate_limited", "retryable_http", "network"}
            retryable_error_count = sum(1 for result in request_results if result.error_kind in retryable_error_kinds)
            nonretryable_error_count = sum(1 for result in request_results if result.error and result.error_kind not in retryable_error_kinds)
            candidate_count = sum(len(result.candidates) for result in request_results)
            fallback_threshold = min(request.min_sources_to_review, MIN_NETWORK_FAILURE_FALLBACK_REVIEWED)
            allow_evidence_backed_fallback = (
                deep_enabled
                and retryable_error_count > 0
                and nonretryable_error_count == 0
                and candidate_count > 0
                and request_reviewed >= fallback_threshold
            )
            if request_reviewed < request.min_sources_to_review and not allow_evidence_backed_fallback:
                rejected += sum(len(result.candidates) for result in request_results)
                LOG.warning(
                    "traveler discovery below minimum review threshold request=%s reviewed=%s required=%s",
                    request.request_id,
                    request_reviewed,
                    request.min_sources_to_review,
                )
                request_updates[request.request_id] = {
                    "status": REQUEST_STATUS_FAILED,
                    "processed_summary": {
                        "reviewed_count": request_reviewed,
                        "accepted_count": 0,
                        "duplicate_count": 0,
                        "rejected_count": rejected - request_rejected_before,
                        "error_count": errors - request_errors_before,
                        "reason": "below_min_sources_to_review",
                    },
                }
                continue
            if allow_evidence_backed_fallback:
                LOG.warning(
                    "traveler discovery using evidence-backed fallback request=%s reviewed=%s required=%s provider_errors=%s",
                    request.request_id,
                    request_reviewed,
                    request.min_sources_to_review,
                    provider_error_count,
                )

            request_limit = request.max_candidates if request.max_candidates is not None else max_to_record
            request_recorded = 0
            for candidate in _dedupe_candidates(candidate for result in request_results for candidate in result.candidates):
                if accepted >= max_to_record:
                    break
                if request_recorded >= request_limit:
                    break
                evidence_record: dict[str, Any] | None = None
                evidence_summary = ""
                evidence_status = "metadata_only"
                evidence_confidence: float | None = None
                if deep_enabled:
                    evidence_record = collect_evidence_for_url(
                        candidate.url,
                        request_id=request.request_id,
                        provider=candidate.provider,
                        query=request.topic,
                        topic=request.topic,
                        fetcher=evidence_fetcher,
                    )
                    evidence_count += 1
                    decision = evidence_record.get("decision", {}) if isinstance(evidence_record, dict) else {}
                    override = load_scoring()["curated_static_override"]
                    if (
                        decision.get("candidate_state") != "accepted"
                        and decision.get("rejection_class") == "low_relevance"
                        and request.discovery_mode == "autonomous_scout"
                        and candidate.provider == "static-technical-sources"
                        and candidate.source_type in set(override["source_types"])
                        and (evidence_record.get("fetch", {}) if isinstance(evidence_record, dict) else {}).get("status") == "ok"
                    ):
                        decision = {
                            **decision,
                            "candidate_state": "accepted",
                            "reason": "curated_static_source_surface_requires_review",
                            "rejection_class": "",
                            "confidence_score": override["confidence_score"],
                        }
                        evidence_record["decision"] = decision
                    if not dry_run:
                        append_evidence(evidence_queue, evidence_record)
                    if decision.get("candidate_state") != "accepted":
                        evidence_rejected_count += 1
                        rejected += 1
                        LOG.info("traveler deep research rejected url=%s reason=%s", candidate.url, decision.get("reason"))
                        continue
                    extract = evidence_record.get("extract", {})
                    fetch = evidence_record.get("fetch", {})
                    evidence_status = "fetched" if fetch.get("status") == "ok" else str(fetch.get("status") or "metadata_only")
                    evidence_summary = str(extract.get("summary_excerpt") or extract.get("title") or decision.get("reason") or "")
                    evidence_confidence = float(decision.get("confidence_score") or 0.0)
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
                    evidence_id=str((evidence_record or {}).get("evidence_id") or ""),
                    evidence_status=evidence_status,
                    evidence_summary=evidence_summary,
                    evidence_confidence=evidence_confidence,
                    discovery_mode=request.discovery_mode,
                    scout_topic_id=request.scout_topic_id,
                    scout_priority=request.scout_priority,
                    topic_source=request.topic_source,
                    paperwiki_interest_slug=request.paperwiki_interest_slug,
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
                    request_recorded += 1
                elif record is not None and record.duplicate:
                    duplicate += 1
                else:
                    accepted += 1
                    request_recorded += 1
            request_accepted = accepted - request_accepted_before
            request_duplicate = duplicate - request_duplicate_before
            request_errors = errors - request_errors_before
            request_rejected = rejected - request_rejected_before
            if request_accepted or request_duplicate:
                status = REQUEST_STATUS_COMPLETED
                reason = "candidates_recorded"
            elif request_errors:
                status = REQUEST_STATUS_FAILED
                reason = "provider_or_evidence_errors"
            else:
                status = REQUEST_STATUS_COMPLETED_EMPTY
                reason = "no_candidates_recorded"
            request_updates[request.request_id] = {
                "status": status,
                "processed_summary": {
                    "reviewed_count": request_reviewed,
                    "accepted_count": request_accepted,
                    "duplicate_count": request_duplicate,
                    "rejected_count": request_rejected,
                    "error_count": request_errors,
                    "max_candidates": request.max_candidates,
                    "reason": reason,
                },
            }
            if accepted >= max_to_record:
                break

    if not dry_run:
        _mark_processed_requests(research_queue, request_updates)

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
        evidence_path=str(evidence_queue) if deep_enabled else None,
        evidence_count=evidence_count,
        evidence_rejected_count=evidence_rejected_count,
        deep_research_enabled=deep_enabled,
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
    parser.add_argument("--deep-research", action="store_true", help="Force safe bounded HTML/RSS/Atom evidence collection before candidate promotion.")
    parser.add_argument("--no-deep-research", action="store_true", help="Disable evidence collection for legacy metadata-only compatibility.")
    parser.add_argument("--deep-research-dry-run", action="store_true", help="Enable deep research evidence collection while keeping candidate/evidence writes disabled.")
    parser.add_argument("--research-queue", type=Path, default=None, help="Override Traveler research-request JSONL path.")
    parser.add_argument("--source-queue", type=Path, default=None, help="Override default Traveler source-candidate JSONL path.")
    parser.add_argument("--max-candidates", type=int, default=None, help="Maximum new candidates to append in this run.")
    parser.add_argument("--status-path", type=Path, default=None, help="Status JSON path. Defaults to env or workspace state path.")
    parser.add_argument("--evidence-path", type=Path, default=None, help="Evidence JSONL path for deep research runs.")
    parser.add_argument("--timeout-sec", type=float, default=20.0, help="HTTP timeout per provider request.")
    return parser


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = _build_parser().parse_args(argv)
    status_path = args.status_path or Path(
        os.environ.get("JIPHYEONJEON_TRAVELER_DISCOVERY_STATUS_PATH", str(default_discovery_status_path()))
    ).expanduser()
    try:
        summary = asyncio.run(
            discover_sources(
                research_queue_path=args.research_queue,
                default_candidate_queue_path=args.source_queue,
                max_candidates=args.max_candidates,
                status_path=status_path,
                dry_run=args.dry_run or args.deep_research_dry_run,
                timeout_sec=args.timeout_sec,
                deep_research=False if args.no_deep_research else (True if args.deep_research or args.deep_research_dry_run else None),
                evidence_path=args.evidence_path,
            )
        )
    except Exception as exc:
        print(f"traveler source discovery error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(summary.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
