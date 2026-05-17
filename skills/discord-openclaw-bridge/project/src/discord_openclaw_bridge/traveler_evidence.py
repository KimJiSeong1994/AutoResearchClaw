"""Safe evidence collection for 집현전-여행자 deep source research.

This module intentionally fetches only bounded public HTML/RSS/Atom metadata. It
persists evidence summaries and excerpts, never full response bodies.
"""
from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .miner import append_jsonl, clean_text, sanitize_url

DEFAULT_EVIDENCE_PATH = Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-traveler" / "evidence.jsonl"
DEFAULT_USER_AGENT = "AutoResearchClaw-TravelerEvidence/0.1"
_ALLOWED_CONTENT_MARKERS = ("html", "xml", "rss", "atom")
_DESCRIPTION_RE = re.compile(
    r"<meta\s+[^>]*(?:name|property)=[\"'](?:description|og:description)[\"'][^>]*content=[\"']([^\"']+)[\"'][^>]*>",
    flags=re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class FetchLimits:
    timeout_sec: float = 8.0
    max_bytes: int = 500_000
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def from_env(cls) -> "FetchLimits":
        return cls(
            timeout_sec=float(os.environ.get("JIPHYEONJEON_TRAVELER_RESEARCH_TIMEOUT_SEC", "8")),
            max_bytes=int(os.environ.get("JIPHYEONJEON_TRAVELER_RESEARCH_MAX_BYTES", "500000")),
            user_agent=os.environ.get("JIPHYEONJEON_TRAVELER_RESEARCH_USER_AGENT", DEFAULT_USER_AGENT),
        )


@dataclass(frozen=True)
class FetchResult:
    status: str
    url: str
    canonical_url: str = ""
    http_status: int | None = None
    content_type: str = ""
    bytes_read: int = 0
    body: str = ""
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and bool(self.body)


@dataclass(frozen=True)
class ExtractedEvidence:
    extractor: str
    title: str = ""
    summary_excerpt: str = ""
    published_or_updated: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    item_count: int = 0

    @property
    def useful(self) -> bool:
        return bool(self.title or self.summary_excerpt or self.item_count)


@dataclass(frozen=True)
class EvidenceDecision:
    candidate_state: str
    reason: str
    rejection_class: str = ""
    confidence_score: float = 0.0


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    request_id: str
    lead_id: str
    provider: str
    query: str
    url: str
    canonical_url: str
    fetched_at: str
    fetch: dict[str, Any]
    extract: dict[str, Any]
    decision: dict[str, Any]
    candidate_id: str | None = None


def default_evidence_path() -> Path:
    return Path(os.environ.get("JIPHYEONJEON_TRAVELER_EVIDENCE_PATH", str(DEFAULT_EVIDENCE_PATH))).expanduser()


def evidence_id_for(*, request_id: str, provider: str, url: str) -> str:
    digest = hashlib.sha256(f"{request_id}\n{provider}\n{url}".encode("utf-8")).hexdigest()[:16]
    return f"traveler_evidence_{digest}"


def lead_id_for(*, provider: str, url: str, title: str = "") -> str:
    digest = hashlib.sha256(f"{provider}\n{url}\n{title}".encode("utf-8")).hexdigest()[:16]
    return f"traveler_lead_{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolved_host_is_public(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr_infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not addr_infos:
        return False
    for info in addr_infos:
        try:
            ip = ipaddress.ip_address(str(info[4][0]).strip("[]"))
        except (ValueError, IndexError):
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _safe_evidence_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
        return False
    return _resolved_host_is_public(parsed.hostname)


class _EvidenceRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not _safe_evidence_url(newurl):
            raise HTTPError(req.full_url, code, "redirect blocked: non-public resolved host", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_evidence_url_open(url: str, *, timeout: float, user_agent: str):
    if not _safe_evidence_url(url):
        raise ValueError("non_public_resolved_host")
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html, application/rss+xml, application/atom+xml, application/xml"})
    return build_opener(_EvidenceRedirectHandler()).open(request, timeout=timeout)


def fetch_public_evidence(url: str, *, limits: FetchLimits | None = None) -> FetchResult:
    """Fetch a bounded public HTML/RSS/Atom document with redirect host checks."""

    safe_url = sanitize_url(url)
    if not safe_url:
        return FetchResult(status="blocked", url=str(url), reason="non_public_or_unsafe_url")
    limits = limits or FetchLimits.from_env()
    try:
        with _safe_evidence_url_open(safe_url, timeout=limits.timeout_sec, user_agent=limits.user_agent) as response:
            content_type = response.headers.get("Content-Type", "")
            if content_type and not any(marker in content_type.lower() for marker in _ALLOWED_CONTENT_MARKERS):
                return FetchResult(
                    status="blocked",
                    url=safe_url,
                    canonical_url=str(response.geturl() or safe_url),
                    http_status=getattr(response, "status", None),
                    content_type=content_type,
                    reason="unsupported_content_type",
                )
            raw = response.read(max(0, limits.max_bytes) + 1)
            if len(raw) > limits.max_bytes:
                return FetchResult(
                    status="blocked",
                    url=safe_url,
                    canonical_url=str(response.geturl() or safe_url),
                    http_status=getattr(response, "status", None),
                    content_type=content_type,
                    bytes_read=limits.max_bytes,
                    reason="response_too_large",
                )
            return FetchResult(
                status="ok",
                url=safe_url,
                canonical_url=sanitize_url(response.geturl() or safe_url) or safe_url,
                http_status=getattr(response, "status", None),
                content_type=content_type,
                bytes_read=len(raw),
                body=raw.decode("utf-8", "replace"),
            )
    except HTTPError as exc:
        return FetchResult(status="failed", url=safe_url, http_status=exc.code, reason=clean_text(exc.reason, limit=160))
    except (OSError, URLError, ValueError) as exc:
        return FetchResult(status="failed", url=safe_url, reason=clean_text(exc, limit=160))


def _strip_markup(text: str) -> str:
    return clean_text(html.unescape(_TAG_RE.sub(" ", text)), limit=500)


def _topic_terms(topic: str) -> list[str]:
    terms: list[str] = []
    generic_terms = {
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "using",
        "source",
        "sources",
        "public",
        "official",
        "research",
        "blog",
        "paper",
        "papers",
        "article",
        "articles",
        "technical",
        "engineering",
        "system",
        "systems",
    }
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9+-]{2,}|[가-힣]{2,}", topic):
        key = raw.lower()
        if key in generic_terms:
            continue
        if key not in terms:
            terms.append(key)
    return terms[:8]


def _keywords(topic: str, title: str, summary: str) -> list[str]:
    corpus = f"{title} {summary}".lower()
    return [key for key in _topic_terms(topic) if key in corpus][:8]


def extract_evidence(fetch: FetchResult, *, topic: str = "") -> ExtractedEvidence:
    if not fetch.ok:
        return ExtractedEvidence(extractor="none")
    content_type = fetch.content_type.lower()
    body = fetch.body
    if "xml" in content_type or "rss" in content_type or "atom" in content_type or body.lstrip().startswith("<?xml"):
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return _extract_html(body, topic=topic)
        return _extract_feed(root, topic=topic)
    return _extract_html(body, topic=topic)


def _extract_html(body: str, *, topic: str) -> ExtractedEvidence:
    title_match = _TITLE_RE.search(body)
    title = _strip_markup(title_match.group(1)) if title_match else ""
    description_match = _DESCRIPTION_RE.search(body)
    summary = clean_text(html.unescape(description_match.group(1)), limit=300) if description_match else ""
    return ExtractedEvidence(
        extractor="html_metadata_v1",
        title=title,
        summary_excerpt=summary,
        matched_keywords=_keywords(topic, title, summary),
    )


def _extract_feed(root: ET.Element, *, topic: str) -> ExtractedEvidence:
    def local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    title = ""
    updated = ""
    item_count = 0
    summaries: list[str] = []
    for elem in root.iter():
        name = local(elem.tag)
        text = clean_text(elem.text, limit=240)
        if not title and name == "title" and text:
            title = text
        if not updated and name in {"updated", "pubdate", "lastbuilddate"} and text:
            updated = text
        if name in {"item", "entry"}:
            item_count += 1
        if name in {"summary", "description"} and text and len(summaries) < 3:
            summaries.append(_strip_markup(text))
    summary = clean_text(" | ".join(summaries), limit=300)
    return ExtractedEvidence(
        extractor="feed_metadata_v1",
        title=title,
        summary_excerpt=summary,
        published_or_updated=updated,
        item_count=item_count,
        matched_keywords=_keywords(topic, title, summary),
    )


def decide_evidence(fetch: FetchResult, extracted: ExtractedEvidence, *, topic: str = "") -> EvidenceDecision:
    if fetch.status == "blocked":
        return EvidenceDecision(candidate_state="rejected", reason=fetch.reason or "blocked_by_policy", rejection_class="blocked")
    if fetch.status != "ok":
        return EvidenceDecision(candidate_state="rejected", reason=fetch.reason or "fetch_failed", rejection_class="fetch_failed")
    if not extracted.useful:
        return EvidenceDecision(candidate_state="rejected", reason="no_extractable_public_metadata", rejection_class="no_evidence")
    if _topic_terms(topic) and not extracted.matched_keywords:
        return EvidenceDecision(candidate_state="rejected", reason="no_topic_relevance_evidence", rejection_class="low_relevance")
    score = 0.6
    if extracted.matched_keywords:
        score += min(0.25, len(extracted.matched_keywords) * 0.05)
    if extracted.item_count:
        score += 0.1
    if extracted.published_or_updated:
        score += 0.05
    score = min(score, 0.95)
    return EvidenceDecision(candidate_state="accepted", reason="bounded_public_evidence_observed", confidence_score=round(score, 2))


def build_evidence_record(
    *,
    request_id: str,
    lead_id: str,
    provider: str,
    query: str,
    url: str,
    fetch: FetchResult,
    extracted: ExtractedEvidence,
    decision: EvidenceDecision,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    safe_url = sanitize_url(url) or str(url)
    canonical_url = sanitize_url(fetch.canonical_url or safe_url) or safe_url
    return {
        "evidence_id": evidence_id_for(request_id=request_id, provider=provider, url=safe_url),
        "request_id": request_id,
        "lead_id": lead_id,
        "candidate_id": candidate_id,
        "provider": clean_text(provider, limit=120),
        "query": clean_text(query, limit=240),
        "url": safe_url,
        "canonical_url": canonical_url,
        "url_hash": hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16] if canonical_url else "",
        "fetched_at": _utc_now(),
        "fetch": {
            "status": fetch.status,
            "http_status": fetch.http_status,
            "content_type": clean_text(fetch.content_type, limit=120),
            "bytes_read": fetch.bytes_read,
            "reason": clean_text(fetch.reason, limit=200),
        },
        "extract": {
            "extractor": extracted.extractor,
            "title": clean_text(extracted.title, limit=180),
            "summary_excerpt": clean_text(extracted.summary_excerpt, limit=300),
            "published_or_updated": clean_text(extracted.published_or_updated, limit=120),
            "item_count": extracted.item_count,
            "matched_keywords": extracted.matched_keywords[:8],
        },
        "decision": {
            "candidate_state": decision.candidate_state,
            "reason": clean_text(decision.reason, limit=240),
            "rejection_class": clean_text(decision.rejection_class, limit=80),
            "confidence_score": decision.confidence_score,
        },
    }


def collect_evidence_for_url(
    url: str,
    *,
    request_id: str,
    provider: str,
    query: str,
    topic: str,
    fetcher: Callable[[str], FetchResult] | None = None,
) -> dict[str, Any]:
    lead_id = lead_id_for(provider=provider, url=sanitize_url(url) or str(url), title=query)
    fetch = fetcher(url) if fetcher else fetch_public_evidence(url)
    extracted = extract_evidence(fetch, topic=topic)
    decision = decide_evidence(fetch, extracted, topic=topic)
    return build_evidence_record(
        request_id=request_id,
        lead_id=lead_id,
        provider=provider,
        query=query,
        url=url,
        fetch=fetch,
        extracted=extracted,
        decision=decision,
    )


def append_evidence(path: Path, record: dict[str, Any]) -> None:
    # Full response bodies are intentionally not accepted by this writer.
    forbidden = json.dumps(record, ensure_ascii=False).lower()
    if "<html" in forbidden or "<!doctype" in forbidden:
        raise ValueError("evidence records must not contain full HTML bodies")
    append_jsonl(path, record)
