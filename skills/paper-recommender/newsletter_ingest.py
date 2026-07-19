#!/usr/bin/env python3
"""Publish local Google/Gmail newsletter exports into the PaperWiki vault.

This intentionally does **not** authenticate to Google or read a mailbox by
itself.  It accepts a user-supplied local export (Gmail Takeout ``.mbox`` or
sanitized JSONL) and writes a raw-first, idempotent wiki intake:

  - {wiki_root}/raw/newsletters/{date}/items.json
  - {wiki_root}/pages/newsletter-ingest-{date}.md

Only message metadata and extracted research/post URLs are persisted.  Full
email bodies are never written to the wiki output.
"""

from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import mailbox
import os
import re
import sys
from dataclasses import dataclass
from datetime import date as _date
from pathlib import Path
from typing import Iterable, Iterator, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)]}>'\""
_SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth",
    "code",
    "key",
    "password",
    "relay_token",
    "secret",
    "signature",
    "sig",
    "token",
}

_RESEARCH_HOST_HINTS = (
    "arxiv.org",
    "doi.org",
    "openreview.net",
    "semanticscholar.org",
    "paperswithcode.com",
    "aclanthology.org",
    "proceedings.mlr.press",
    "neurips.cc",
    "icml.cc",
    "openai.com",
    "anthropic.com",
    "deepmind.google",
    "ai.googleblog.com",
    "github.com",
)

_ACADEMIC_HOST_HINTS = (
    "arxiv.org",
    "doi.org",
    "openreview.net",
    "semanticscholar.org",
    "paperswithcode.com",
    "aclanthology.org",
    "proceedings.mlr.press",
    "neurips.cc",
    "icml.cc",
)

_TECHNICAL_REPORT_HOST_HINTS = (
    "engineering.fb.com",
    "openai.com",
    "anthropic.com",
    "deepmind.google",
    "ai.googleblog.com",
    "github.com",
    "huggingface.co",
    "pytorch.org",
    "tensorflow.org",
)

_TECHNICAL_SIGNAL_PHRASES = (
    "knowledge graph",
    "semantic search",
    "vector database",
    "machine learning",
    "language model",
    "large language model",
    "tool use",
    "coding agent",
    "llm agent",
    "open source",
    "inference serving",
    "eval pipeline",
    "red team",
)

_TECHNICAL_SIGNAL_TERMS = (
    "academic",
    "agent",
    "algorithm",
    "architecture",
    "arxiv",
    "benchmark",
    "cuda",
    "dataset",
    "database",
    "developer",
    "embedding",
    "evaluation",
    "framework",
    "graph",
    "gpu",
    "inference",
    "knowledge",
    "latency",
    "library",
    "llm",
    "mlops",
    "model",
    "multimodal",
    "paper",
    "rag",
    "reasoning",
    "report",
    "research",
    "retrieval",
    "security",
    "serving",
    "vector",
    "vision",
    "workflow",
    "technical",
    "검색",
    "논문",
    "리서치",
    "모델",
    "벤치마크",
    "에이전트",
    "지식그래프",
)

_OUT_OF_SCOPE_HINTS = (
    "analytics",
    "career ladder",
    "feed/update",
    "funding",
    "hiring",
    "impressions",
    "job alert",
    "job recommendation",
    "jobs/view",
    "login",
    "market",
    "notifications",
    "partnership",
    "preferences",
    "pricing",
    "profile views",
    "settings",
    "signin",
    "signup",
    "terms",
    "unsubscribe",
    "weekly stats",
    "구인",
    "노출수",
    "님 업데이트",
    "업데이트했습니다",
    "지원하기",
    "채용",
    "프로필 조회",
)

_DEFAULT_MAX_MESSAGES = 500

_CONTENT_ANALYSIS_ALLOWED_KEYS = {
    "version",
    "analysis_status",
    "evidence_tier",
    "analysis_provenance",
    "provider",
    "summary_lines",
    "claims",
    "limitations",
    "quota_units",
    "confidence",
    "operator_note_used",
    "source_separation",
    "fetched_at",
    "expires_at",
    "fallback_reason",
    "policy_flags",
}
_CONTENT_ANALYSIS_FORBIDDEN_KEYS = {
    "raw_provider_payload",
    "raw_transcript",
    "caption_text",
    "raw_caption",
    "audio_bytes",
    "audio_path",
    "video_bytes",
    "credential",
    "credentials",
    "access_token",
    "refresh_token",
    "private_body",
}
_CONTENT_ANALYSIS_SENSITIVE_VALUE_MARKERS = (
    "token=",
    "access_token=",
    "refresh_token=",
    "secret=",
    "credential=",
)
_LEGACY_EVIDENCE_TIERS = {
    "gemini_youtube_uri_no_transcript": "model_public_youtube_av_no_raw",
    "model_youtube_uri_no_transcript": "model_public_youtube_av_no_raw",
}
_CONTENT_ANALYSIS_DIRECT_CLAIM_RE = re.compile(
    r"(영상에서\s*말했|영상에서\s*언급|transcript\s*분석\s*결과|자막\s*분석\s*결과|\b\d{1,2}:\d{2}\b\s*[\"'‘’“”])",
    re.IGNORECASE,
)
_CONTENT_ANALYSIS_FORBIDDEN_MARKER_RE = re.compile(
    r"(?:raw_provider_payload|raw_transcript|caption_text|raw_caption|audio_bytes|audio_path|video_bytes|access_token|refresh_token|private_body)\s*[:=]",
    re.IGNORECASE,
)


def _normalize_evidence_tier(value: object) -> str:
    tier = _clean_text(str(value or ""))
    return _LEGACY_EVIDENCE_TIERS.get(tier, tier)


def _has_sensitive_content_analysis_value(value: object) -> bool:
    if isinstance(value, str):
        lower = value.lower()
        return any(marker in lower for marker in _CONTENT_ANALYSIS_SENSITIVE_VALUE_MARKERS) or _CONTENT_ANALYSIS_FORBIDDEN_MARKER_RE.search(value) is not None
    if isinstance(value, Mapping):
        return any(_has_sensitive_content_analysis_value(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_sensitive_content_analysis_value(v) for v in value)
    return False


def _content_text_allowed(text: str, evidence_tier: str) -> bool:
    if evidence_tier == "official_caption_ephemeral":
        return True
    # Keep honesty labels such as "자막/transcript 근거 아님" while dropping
    # renderer-visible direct speech/transcript claims for metadata/operator/model tiers.
    return _CONTENT_ANALYSIS_DIRECT_CLAIM_RE.search(text) is None


def _sanitize_content_text(value: object, *, evidence_tier: str, limit: int = 500) -> str:
    text = _clean_text(str(value or ""))[:limit]
    if not text or _has_sensitive_content_analysis_value(text):
        return ""
    if not _content_text_allowed(text, evidence_tier):
        return ""
    return text


def _sanitize_content_analysis(value: object) -> dict[str, object]:
    """Return derived-only YouTube content analysis safe for archive/newsletter output."""
    if not isinstance(value, Mapping):
        return {}
    raw_tier = value.get("evidence_tier") or value.get("analysis_provenance")
    evidence_tier = _normalize_evidence_tier(raw_tier) or "metadata_only"
    out: dict[str, object] = {"evidence_tier": evidence_tier}

    for key in _CONTENT_ANALYSIS_ALLOWED_KEYS:
        if key == "evidence_tier" or key not in value:
            continue
        raw = value.get(key)
        if raw in (None, "", [], {}):
            continue
        if key not in {"claims", "summary_lines", "limitations", "policy_flags"} and _contains_content_analysis_forbidden(raw):
            continue
        if key == "analysis_provenance":
            text = _clean_text(str(raw))
            out[key] = _LEGACY_EVIDENCE_TIERS.get(text, text)[:180]
        elif key in {"summary_lines", "limitations", "policy_flags"} and isinstance(raw, list):
            limit = 240 if key != "policy_flags" else 120
            lines = [_sanitize_content_text(line, evidence_tier=evidence_tier, limit=limit) for line in raw[:8]]
            clean = [line for line in lines if line]
            if clean:
                out[key] = clean
        elif key == "claims" and isinstance(raw, list):
            claims: list[dict[str, object]] = []
            for claim in raw[:8]:
                if not isinstance(claim, Mapping):
                    continue
                text = _sanitize_content_text(claim.get("text"), evidence_tier=evidence_tier, limit=300)
                if not text:
                    continue
                clean_claim: dict[str, object] = {"text": text}
                basis = _sanitize_content_text(claim.get("basis"), evidence_tier=evidence_tier, limit=120)
                if basis:
                    clean_claim["basis"] = basis
                confidence = claim.get("confidence")
                if isinstance(confidence, (int, float, bool)):
                    clean_claim["confidence"] = confidence
                claims.append(clean_claim)
            if claims:
                out[key] = claims
        elif isinstance(raw, (int, float, bool)):
            out[key] = raw
        elif isinstance(raw, str):
            text = _sanitize_content_text(raw, evidence_tier=evidence_tier, limit=500)
            if text:
                out[key] = text
    if out.get("analysis_status") == "status":
        out.pop("analysis_status", None)
    return out if len(out) > 1 else {}


def _contains_content_analysis_forbidden(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _CONTENT_ANALYSIS_FORBIDDEN_KEYS:
                return True
            if _contains_content_analysis_forbidden(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_content_analysis_forbidden(child) for child in value)
    return _has_sensitive_content_analysis_value(value)

@dataclass(frozen=True)
class TopicRule:
    primary: str
    label: str
    priority: int
    phrases: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    substrings: tuple[str, ...] = ()
    secondary: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopicClassification:
    primary: str
    primary_display: str
    secondary: tuple[str, ...]
    confidence: float
    reasons: tuple[str, ...]
    score: int

    @property
    def label(self) -> str:
        """Backward-compatible display label used by existing callers."""
        return self.primary_display

    @property
    def evidence(self) -> tuple[str, ...]:
        """Backward-compatible sanitized match reasons."""
        return self.reasons


@dataclass(frozen=True)
class ContentEvidence:
    evidence_id: str
    source_type: str
    title: str
    url: str
    kind: str
    sender_or_source: str
    received_or_published_at: str
    public_excerpt: str
    context_digest: str
    private_context_used: bool
    privacy_class: str
    provenance: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type,
            "title": self.title,
            "url": self.url,
            "kind": self.kind,
            "sender_or_source": self.sender_or_source,
            "received_or_published_at": self.received_or_published_at,
            "public_excerpt": self.public_excerpt,
            "context_digest": self.context_digest,
            "private_context_used": self.private_context_used,
            "privacy_class": self.privacy_class,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class ContextDossier:
    evidence_ids: tuple[str, ...]
    claims: tuple[dict[str, str], ...]
    source_diversity: int
    coverage_gaps: tuple[str, ...]
    persistence_policy: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_ids": list(self.evidence_ids),
            "claims": list(self.claims),
            "source_diversity": self.source_diversity,
            "coverage_gaps": list(self.coverage_gaps),
            "persistence_policy": self.persistence_policy,
        }


@dataclass(frozen=True)
class TopicCandidate:
    topic_id: str
    canonical_primary: str
    primary_display: str
    secondary_topics: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    summary_ko: str
    novelty_score: float
    reader_fit_score: float
    evidence_strength: float
    quality_flags: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "topic_id": self.topic_id,
            "canonical_primary": self.canonical_primary,
            "primary_display": self.primary_display,
            "secondary_topics": list(self.secondary_topics),
            "evidence_ids": list(self.evidence_ids),
            "summary_ko": self.summary_ko,
            "novelty_score": self.novelty_score,
            "reader_fit_score": self.reader_fit_score,
            "evidence_strength": self.evidence_strength,
            "quality_flags": list(self.quality_flags),
        }


@dataclass(frozen=True)
class TopicSelectionRun:
    mode: str
    selected_topics: tuple[dict[str, object], ...]
    rejected_topics: tuple[dict[str, object], ...]
    coverage: dict[str, object]
    telemetry: dict[str, object]
    fallback_used: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "selected_topics": list(self.selected_topics),
            "rejected_topics": list(self.rejected_topics),
            "coverage": self.coverage,
            "telemetry": self.telemetry,
            "fallback_used": self.fallback_used,
        }


_TOPIC_RULES: tuple[TopicRule, ...] = (
    TopicRule(
        "data_retrieval_knowledge",
        "검색/RAG/지식그래프",
        10,
        phrases=("knowledge graph", "semantic search", "vector database"),
        terms=("retrieval", "rag", "search", "graph", "knowledge"),
        secondary=("rag", "semantic_search", "knowledge_graph"),
    ),
    TopicRule(
        "agents_automation",
        "LLM/에이전트",
        20,
        phrases=("language model", "tool use", "coding agent", "llm agent"),
        terms=("llm", "agent", "reasoning", "workflow", "autonomous"),
        secondary=("llm", "agent", "automation"),
    ),
    TopicRule(
        "multimodal_vision",
        "멀티모달/비전",
        30,
        phrases=("multimodal model",),
        terms=("multimodal", "vision", "image", "video", "vlm"),
        secondary=("multimodal", "vision", "generative_ai"),
    ),
    TopicRule(
        "ai_infra_mlops",
        "인프라/배포",
        40,
        phrases=("inference serving", "eval pipeline"),
        terms=("inference", "serving", "gpu", "cuda", "deploy", "latency", "benchmark"),
        secondary=("evaluation", "mlops", "compute_cost"),
    ),
    TopicRule(
        "open_source_developer_ecosystem",
        "오픈소스/코드",
        50,
        phrases=("open source", "developer tool"),
        terms=("repo", "repository", "library", "framework"),
        substrings=("github.com",),
        secondary=("open_source", "developer_tools"),
    ),
    TopicRule(
        "safety_governance_regulation",
        "AI 안전/평가",
        60,
        phrases=("red team",),
        terms=("safety", "eval", "evaluation", "alignment", "privacy", "security", "regulation", "copyright"),
        secondary=("evaluation", "safety", "privacy", "security", "regulatory_risk"),
    ),
    TopicRule(
        "market_ecosystem_strategy",
        "산업/제품 동향",
        70,
        phrases=("product launch",),
        terms=("product", "launch", "release", "pricing", "market", "enterprise", "partnership", "funding"),
        secondary=("enterprise", "pricing", "market", "startup"),
    ),
)

_TOPIC_SCORE_THRESHOLD = 2


@dataclass(frozen=True)
class NewsletterMessage:
    subject: str
    sender: str
    received_at: str
    body: str


@dataclass(frozen=True)
class EligibilityDecision:
    verdict: str
    bucket: str
    reason: str
    evidence: tuple[str, ...] = ()

    @property
    def eligible(self) -> bool:
        return self.verdict == "eligible"


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _safe_title(value: str) -> str:
    value = _clean_text(value)
    return value.replace("[[", "[ [").replace("]]", "] ]").replace("|", "\\|")


def _short_hash(value: str, *, prefix: str = "") -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}{digest}" if prefix else digest


def _public_excerpt(item: dict[str, str], *, limit: int = 220) -> str:
    return _clean_text(
        item.get("public_excerpt")
        or item.get("summary")
        or item.get("article_description")
        or item.get("snippet")
        or item.get("title")
        or ""
    )[:limit]


def _private_context(item: dict[str, str]) -> str:
    return _clean_text(item.get("classification_text") or item.get("body") or item.get("text") or "")


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out: list[str] = []
    for payload, charset in parts:
        if isinstance(payload, bytes):
            out.append(payload.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(payload)
    return _clean_text("".join(out))


def _message_body(msg: mailbox.mboxMessage) -> str:
    """Return decoded text/html body for URL extraction only."""
    chunks: list[str] = []
    parts: Iterable[email.message.Message]
    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]
    for part in parts:
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None
        if payload is None:
            raw_payload = part.get_payload()
            if isinstance(raw_payload, str):
                chunks.append(raw_payload)
            continue
        if isinstance(payload, bytes):
            charset = part.get_content_charset() or "utf-8"
            chunks.append(payload.decode(charset, errors="replace"))
    return "\n".join(chunks)


def load_mbox(path: Path) -> Iterator[NewsletterMessage]:
    for msg in mailbox.mbox(path):
        yield NewsletterMessage(
            subject=_decode_header(msg.get("subject")),
            sender=_decode_header(msg.get("from")),
            received_at=_clean_text(msg.get("date")),
            body=_message_body(msg),
        )


def load_jsonl(path: Path) -> Iterator[NewsletterMessage]:
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
            body = raw.get("body") or raw.get("text") or raw.get("html") or ""
            yield NewsletterMessage(
                subject=_clean_text(str(raw.get("subject") or "")),
                sender=_clean_text(str(raw.get("from") or raw.get("sender") or "")),
                received_at=_clean_text(str(raw.get("date") or raw.get("received_at") or "")),
                body=str(body),
            )


def iter_messages(path: Path) -> Iterator[NewsletterMessage]:
    suffix = path.suffix.lower()
    if suffix in {".mbox", ".mbx"}:
        yield from load_mbox(path)
        return
    if suffix in {".jsonl", ".ndjson"}:
        yield from load_jsonl(path)
        return
    raise ValueError(f"unsupported source type for {path}; expected .mbox or .jsonl")


def load_messages(path: Path, *, max_messages: int | None = None) -> list[NewsletterMessage]:
    messages: list[NewsletterMessage] = []
    for idx, msg in enumerate(iter_messages(path), start=1):
        if max_messages is not None and idx > max_messages:
            break
        messages.append(msg)
    return messages


def enforce_source_size(path: Path, *, max_source_bytes: int | None) -> None:
    if max_source_bytes is None:
        return
    size = path.stat().st_size
    if size > max_source_bytes:
        raise ValueError(
            f"source export is {size} bytes, above --max-source-bytes={max_source_bytes}; "
            "split or sanitize the export first"
        )


def extract_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    unescaped = html.unescape(text)
    for match in _URL_RE.finditer(unescaped):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def sanitize_public_url(url: str) -> str:
    """Strip tracking and credential-like query parameters before persisting/posting."""
    text = url.strip().rstrip(_TRAILING_PUNCT)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower()
        not in {
            "fbclid",
            "gclid",
            "igshid",
            "mc_cid",
            "mc_eid",
            "ref",
            *_SENSITIVE_QUERY_KEYS,
        }
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def classify_url(url: str) -> str:
    lower = url.lower()
    if "arxiv.org/abs/" in lower or "arxiv.org/pdf/" in lower:
        return "paper:arxiv"
    if "doi.org/" in lower:
        return "paper:doi"
    if any(host in lower for host in ("openreview.net", "semanticscholar.org", "aclanthology.org", "proceedings.mlr.press")):
        return "paper"
    if "github.com/" in lower:
        return "code"
    if any(host in lower for host in ("openai.com", "anthropic.com", "deepmind.google", "ai.googleblog.com")):
        return "research-post"
    return "post"


def is_research_url(url: str) -> bool:
    lower = url.lower()
    return any(hint in lower for hint in _RESEARCH_HOST_HINTS)


def is_private_utility_url(url: str) -> bool:
    lower = url.lower()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    path_parts = {part.lower() for part in parsed.path.split("/") if part}
    if host in {"myaccount.google.com", "accounts.google.com", "mail.google.com"}:
        return True
    if host == "support.google.com" and path_parts & {"accounts", "analytics"}:
        return True
    blocked_path_parts = {
        "account",
        "login",
        "preferences",
        "settings",
        "signin",
        "signup",
        "terms",
        "unsubscribe",
    }
    if path_parts & blocked_path_parts:
        return True
    if "google.com/analytics/answer" in lower:
        return True
    try:
        query_keys = {key.lower() for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}
    except ValueError:
        return False
    return bool(query_keys & _SENSITIVE_QUERY_KEYS)


def _hostname_matches(host: str, hint: str) -> bool:
    host = host.lower().removeprefix("www.")
    hint = hint.lower().removeprefix("www.")
    return host == hint or host.endswith(f".{hint}")


def _has_public_technical_signal(text: str) -> tuple[int, list[str]]:
    lower = text.lower()
    score = 0
    evidence: list[str] = []
    for phrase in _TECHNICAL_SIGNAL_PHRASES:
        if _has_token_phrase(lower, phrase):
            score += 3
            evidence.append(phrase)
    for term in _TECHNICAL_SIGNAL_TERMS:
        if _has_token_phrase(lower, term):
            score += 1
            evidence.append(term)
    # URL/path tokens often use separators, so keep a bounded substring path
    # for signals such as `graph-rag`, `llm-agent`, or `cuda`.
    for term in ("rag", "llm", "agent", "benchmark", "research", "paper", "retrieval", "graph", "model"):
        if term in lower.replace("-", " ").replace("_", " ") and term not in evidence:
            score += 1
            evidence.append(term)
    return score, evidence[:5]


def _contains_out_of_scope_hint(text: str) -> str:
    lower = text.lower()
    for hint in _OUT_OF_SCOPE_HINTS:
        if hint in lower:
            return hint
    return ""


def academic_technical_eligibility(item: Mapping[str, object]) -> EligibilityDecision:
    """Classify whether an item belongs in academic-search/technical-report intake.

    This mirrors the `academic-technical-filter` agent skill: use public URL,
    title, article metadata, and public summaries only; never private mailbox
    bodies or secret values.
    """

    url = sanitize_public_url(str(item.get("url") or ""))
    title = _clean_text(str(item.get("article_title") or item.get("title") or ""))
    public_text = " ".join(
        _clean_text(str(item.get(key) or ""))
        for key in (
            "article_description",
            "public_excerpt",
            "summary",
            "snippet",
            "kind",
            "primary_topic_display",
        )
    )
    media = item.get("media")
    if isinstance(media, Mapping):
        public_text = " ".join(
            [
                public_text,
                _clean_text(str(media.get("channel_title") or "")),
                _clean_text(str(media.get("analysis_status") or "")),
                _clean_text(str(media.get("analysis_provenance") or "")),
            ]
        )
    summary_lines = item.get("summary_lines") or item.get("summaryLines") or []
    if isinstance(summary_lines, list):
        public_text = " ".join([public_text, *(_clean_text(str(line)) for line in summary_lines)])
    haystack = _clean_text(" ".join([url, title, public_text]))
    lower_haystack = haystack.lower()
    try:
        parsed_url = urlsplit(url)
        host = (parsed_url.hostname or "").lower()
        path_parts = [part for part in parsed_url.path.split("/") if part]
    except ValueError:
        host = ""
        path_parts = []

    if not url:
        return EligibilityDecision("reject", "out_of_scope", "missing_public_url")
    if is_private_utility_url(url):
        return EligibilityDecision("reject", "out_of_scope", "private_or_utility_url")

    if any(_hostname_matches(host, hint) for hint in _ACADEMIC_HOST_HINTS):
        return EligibilityDecision("eligible", "academic_search", "academic_host", (host,))

    if _hostname_matches(host, "github.com") and len(path_parts) >= 2:
        return EligibilityDecision("eligible", "technical_report", "open_source_repository", ("github.com",))

    out_of_scope = _contains_out_of_scope_hint(lower_haystack)
    score, evidence = _has_public_technical_signal(haystack)
    if out_of_scope and score < 3:
        return EligibilityDecision("reject", "out_of_scope", f"non_technical_hint:{out_of_scope}", tuple(evidence))

    if any(_hostname_matches(host, hint) for hint in _TECHNICAL_REPORT_HOST_HINTS) and score >= 1:
        return EligibilityDecision("eligible", "technical_report", "technical_host_with_signal", tuple(evidence))

    if score >= 2:
        return EligibilityDecision("eligible", "technical_report", "public_technical_signal", tuple(evidence))

    return EligibilityDecision("reject", "out_of_scope", "insufficient_academic_or_technical_signal", tuple(evidence))


def select_items(
    messages: Iterable[NewsletterMessage],
    *,
    sender_allowlist: list[str],
    include_all_urls: bool = False,
) -> list[dict[str, str]]:
    allow = [s.lower() for s in sender_allowlist]
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for msg in messages:
        sender_lower = msg.sender.lower()
        if allow and not any(token in sender_lower for token in allow):
            continue
        for raw_url in extract_urls(msg.body):
            url = sanitize_public_url(raw_url)
            if is_private_utility_url(url):
                continue
            if not include_all_urls and not is_research_url(url):
                continue
            candidate = {
                "title": msg.subject or "(untitled newsletter item)",
                "url": url,
                "kind": classify_url(url),
                "sender": msg.sender,
                "received_at": msg.received_at,
            }
            if not academic_technical_eligibility(candidate).eligible:
                continue
            key = (msg.subject, url)
            if key in seen:
                continue
            seen.add(key)
            items.append(candidate)
    return items


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def render_page(*, run_date: str, items: list[dict[str, str]], source_name: str) -> str:
    out = [
        "---",
        f'date: "{run_date}"',
        "type: newsletter-ingest",
        "tags:",
        "  - newsletters",
        "  - llm-wiki",
        "---",
        f"# Newsletter intake — {run_date}",
        "",
        "> [!info] Privacy boundary",
        "> Generated from a user-provided local export. Full email bodies and secret values are not stored in this page.",
        "",
        f"- Source export: `{source_name}`",
        f"- Extracted items: {len(items)}",
        "",
    ]
    if not items:
        out += ["No research/post URLs matched the configured filters.", ""]
    else:
        out += ["## Items", ""]
        for item in items:
            title = _safe_title(item["title"])
            sender = _safe_title(item["sender"])
            out.append(f"- **{title}** — [{item['kind']}]({item['url']})")
            if sender or item["received_at"]:
                out.append(f"  - from: {sender or 'unknown'} · received: {item['received_at'] or 'unknown'}")
        out.append("")
    out.append("*Generated by `newsletter_ingest.py`*")
    return "\n".join(out) + "\n"


def publish_items(
    *,
    wiki_root: Path,
    run_date: str,
    source_path: Path,
    items: list[dict[str, str]],
) -> tuple[Path, Path]:
    raw_dir = wiki_root / "raw" / "newsletters" / run_date
    pages_dir = wiki_root / "pages"
    raw_dir.mkdir(parents=True, exist_ok=True)
    pages_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / "items.json"
    page_path = pages_dir / f"newsletter-ingest-{run_date}.md"
    payload = {
        "date": run_date,
        "source_file": source_path.name,
        "privacy": "metadata-and-extracted-urls-only; full email bodies omitted",
        "topic_selection_mode": os.environ.get("TOPIC_SELECTOR_MODE", "legacy"),
        "items": [_item_for_publish(item) for item in items],
    }
    _atomic_write_text(raw_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write_text(
        page_path,
        render_page(run_date=run_date, items=items, source_name=source_path.name),
    )
    return raw_path, page_path


def _has_token_phrase(text: str, phrase: str) -> bool:
    phrase = phrase.lower().strip()
    if not phrase:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(phrase).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


def _score_topic_rule(text: str, rule: TopicRule) -> TopicClassification | None:
    score = 0
    reasons: list[str] = []
    secondary: list[str] = []

    def add_secondary() -> None:
        for tag in rule.secondary:
            if tag not in secondary:
                secondary.append(tag)

    for phrase in rule.phrases:
        if _has_token_phrase(text, phrase):
            score += 4
            reasons.append(phrase)
            add_secondary()
    for term in rule.terms:
        if _has_token_phrase(text, term):
            score += 2
            reasons.append(term)
            add_secondary()
    lower = text.lower()
    for token in rule.substrings:
        if token.lower() in lower:
            score += 2
            reasons.append(token)
            add_secondary()
    if score < _TOPIC_SCORE_THRESHOLD:
        return None
    return TopicClassification(
        primary=rule.primary,
        primary_display=rule.label,
        secondary=tuple(secondary[:7]),
        confidence=min(1.0, score / 10),
        reasons=tuple(reasons),
        score=score,
    )


def classify_topic_result(item: dict[str, str]) -> TopicClassification:
    haystack = " ".join(
        [
            item.get("title", ""),
            item.get("kind", ""),
            item.get("url", ""),
            item.get("snippet", ""),
            item.get("summary", ""),
            item.get("public_excerpt", ""),
            item.get("article_title", ""),
            item.get("article_description", ""),
            item.get("classification_text", ""),
        ]
    )
    matches = [
        result
        for rule in _TOPIC_RULES
        if (result := _score_topic_rule(haystack, rule)) is not None
    ]
    if matches:
        priority = {rule.label: rule.priority for rule in _TOPIC_RULES}
        return sorted(matches, key=lambda result: (-result.score, priority[result.label]))[0]
    if item.get("kind", "").startswith("paper"):
        return TopicClassification(
            primary="research_paper_general",
            primary_display="논문/리서치",
            secondary=("research",),
            confidence=0.1,
            reasons=("paper-kind",),
            score=1,
        )
    return TopicClassification(
        primary="other_tech_report",
        primary_display="기타 테크 리포트",
        secondary=(),
        confidence=0.0,
        reasons=(),
        score=0,
    )


def classify_topic_detail(item: dict[str, str]) -> TopicClassification:
    return classify_topic_result(item)


def classify_topic(item: dict[str, str]) -> str:
    return classify_topic_result(item).primary_display


def build_content_evidence(item: dict[str, str], *, source_type: str = "newsletter") -> ContentEvidence:
    """Build a privacy-preserving evidence record for topic selection.

    Private body/classification text may influence the context digest, but it is
    never copied into the evidence payload.
    """
    title = _clean_text(item.get("title") or "(untitled newsletter item)")
    url = _clean_text(item.get("url") or "")
    kind = _clean_text(item.get("kind") or "post")
    sender = _clean_text(item.get("sender") or "")
    received = _clean_text(item.get("received_at") or "")
    public_excerpt = _public_excerpt(item)
    private_context = _private_context(item)
    digest_basis = "\n".join([title, url, kind, public_excerpt, private_context])
    evidence_id = _short_hash("\n".join([source_type, title, url, kind]), prefix="ev_")
    private_used = bool(private_context)
    return ContentEvidence(
        evidence_id=evidence_id,
        source_type=source_type,
        title=title,
        url=url,
        kind=kind,
        sender_or_source=sender,
        received_or_published_at=received,
        public_excerpt=public_excerpt,
        context_digest=_short_hash(digest_basis),
        private_context_used=private_used,
        privacy_class="private_context_used_not_persisted" if private_used else "metadata_and_public_excerpt",
        provenance="newsletter_ingest",
    )


def build_context_dossier(evidence: ContentEvidence, classification: TopicClassification) -> ContextDossier:
    claims = (
        {"role": "core", "text": evidence.title},
        {"role": "technical", "text": ", ".join(classification.reasons) or "fallback"},
        {"role": "limitation", "text": "Full private email body is not persisted."},
    )
    gaps: list[str] = []
    if not evidence.public_excerpt:
        gaps.append("missing_public_excerpt")
    if classification.primary == "other_tech_report":
        gaps.append("weak_topic_signal")
    return ContextDossier(
        evidence_ids=(evidence.evidence_id,),
        claims=claims,
        source_diversity=1,
        coverage_gaps=tuple(gaps),
        persistence_policy="metadata-and-extracted-urls-only; private context digest only",
    )


def build_topic_candidate(
    evidence: ContentEvidence,
    dossier: ContextDossier,
    classification: TopicClassification,
) -> TopicCandidate:
    quality_flags: list[str] = []
    if evidence.private_context_used:
        quality_flags.append("private_context_used_not_persisted")
    if classification.primary == "other_tech_report":
        quality_flags.append("weak_topic_signal")
    topic_id = f"{classification.primary}:{evidence.evidence_id}"
    return TopicCandidate(
        topic_id=topic_id,
        canonical_primary=classification.primary,
        primary_display=classification.primary_display,
        secondary_topics=classification.secondary,
        evidence_ids=dossier.evidence_ids,
        summary_ko=f"{classification.primary_display} 후보: {evidence.title}",
        novelty_score=0.0,
        reader_fit_score=0.0,
        evidence_strength=classification.confidence,
        quality_flags=tuple(quality_flags),
    )


def analyze_topic_context(item: dict[str, str], *, mode: str | None = None) -> dict[str, object]:
    """Return canonical shadow topic-selection telemetry for one item.

    This is intentionally deterministic for Phase 0/1. It creates the schema
    seam where an agent selector can later replace or augment the candidate
    scoring while preserving privacy and renderer contracts.
    """
    selected_mode = (mode or os.environ.get("TOPIC_SELECTOR_MODE") or "legacy").strip().lower()
    if selected_mode not in {"legacy", "shadow", "dual", "agent"}:
        selected_mode = "legacy"
    classification = classify_topic_result(item)
    evidence = build_content_evidence(item)
    dossier = build_context_dossier(evidence, classification)
    candidate = build_topic_candidate(evidence, dossier, classification)
    selection = TopicSelectionRun(
        mode=selected_mode,
        selected_topics=(
            {
                "rank": 1,
                "topic_id": candidate.topic_id,
                "canonical_primary": candidate.canonical_primary,
                "primary_display": candidate.primary_display,
                "secondary_topics": list(candidate.secondary_topics),
                "rationale_ko": "Phase 1 shadow selector uses deterministic classifier output as the baseline.",
                "technical_point": ", ".join(classification.reasons) or "fallback",
                "researcher_action": "관련 원문을 확인하고 후속 읽기/아카이브 우선순위를 정한다.",
                "evidence_ids": list(candidate.evidence_ids),
                "confidence": classification.confidence,
                "flags": list(candidate.quality_flags),
            },
        ),
        rejected_topics=(),
        coverage={
            "topic_counts": {classification.primary: 1},
            "source_counts": {evidence.source_type: 1},
            "missing_axes": list(dossier.coverage_gaps),
            "max_topic_share": 1.0,
        },
        telemetry={
            "privacy_violation_count": 0,
            "parity_mismatch_count": 0,
            "agent_fallback_used": selected_mode in {"legacy", "shadow", "dual"},
            "private_context_used": evidence.private_context_used,
        },
        fallback_used=classification.primary in {"research_paper_general", "other_tech_report"},
    )
    return {
        "evidence": evidence.to_dict(),
        "context_dossier": dossier.to_dict(),
        "topic_candidate": candidate.to_dict(),
        "topic_selection": selection.to_dict(),
    }


def _item_for_publish(item: dict[str, str]) -> dict[str, object]:
    allowed = {
        "title",
        "url",
        "kind",
        "sender",
        "received_at",
        "article_title",
        "article_description",
        "public_excerpt",
        "summary_lines",
        "primary_topic",
        "primary_topic_display",
        "secondary_topics",
        "topic_confidence",
        "topic_reasons",
        "media",
        "content_analysis",
    }
    out: dict[str, object] = {}
    for key, value in item.items():
        if key not in allowed:
            continue
        if key == "media":
            sanitized_media = _sanitize_media(value)
            if sanitized_media:
                out[key] = sanitized_media
        elif key == "content_analysis":
            sanitized_analysis = _sanitize_content_analysis(value)
            if sanitized_analysis:
                out[key] = sanitized_analysis
        else:
            out[key] = value
    classification = classify_topic_result(item)
    out.setdefault("primary_topic", classification.primary)
    out.setdefault("primary_topic_display", classification.primary_display)
    out.setdefault("secondary_topics", list(classification.secondary))
    out.setdefault("topic_confidence", classification.confidence)
    out.setdefault("topic_reasons", list(classification.reasons))
    out["topic_context"] = analyze_topic_context(item, mode="shadow")
    return out


def item_summary_lines(item: Mapping[str, object]) -> list[str]:
    raw = item.get("summary_lines") or item.get("summaryLines") or []
    lines: list[str] = []
    if isinstance(raw, list):
        for value in raw:
            text = _clean_text(str(value))
            if text and text not in lines:
                lines.append(text[:220])
            if len(lines) == 3:
                return lines
    public_excerpt = _clean_text(
        str(item.get("public_excerpt") or item.get("article_description") or item.get("snippet") or "")
    )
    title = _clean_text(str(item.get("article_title") or item.get("title") or ""))
    classification = classify_topic_result(item)  # type: ignore[arg-type]
    fallback = [
        public_excerpt or title or "공개 원문 요약이 부족해 제목과 메타데이터 중심으로 추적합니다.",
        f"{classification.primary_display} 신호: {', '.join(classification.reasons) or item.get('kind') or 'metadata'} 기준으로 분류했습니다.",
        f"후속 검토 포인트: {title or item.get('url') or '원문'}의 기술 방법, 평가 지표, 적용 범위를 확인합니다.",
    ]
    for value in fallback:
        text = _clean_text(str(value))
        if text and text not in lines:
            lines.append(text[:220])
        if len(lines) == 3:
            break
    while len(lines) < 3:
        lines.append("공개 원문 근거가 부족해 다음 수집에서 상세 내용을 재확인합니다.")
    return lines[:3]


def _sanitize_media(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "type", "platform", "video_id", "canonical_url", "original_url", "start_seconds",
        "playlist_id", "channel_title", "duration", "published_at", "provider", "parts",
        "etag", "metadata_provenance", "analysis_provenance", "analysis_status",
        "confidence", "fetched_at", "expires_at", "quota_units",
    }
    out: dict[str, object] = {}
    for key in allowed:
        raw = value.get(key)
        if raw in (None, "", [], {}):
            continue
        if key == "parts" and isinstance(raw, list):
            out[key] = [_clean_text(str(part))[:60] for part in raw[:8] if _clean_text(str(part))]
        elif isinstance(raw, (int, float, bool)):
            out[key] = raw
        else:
            text = _clean_text(str(raw))
            if key in {"analysis_provenance", "metadata_provenance"}:
                text = _LEGACY_EVIDENCE_TIERS.get(text, text)
            if any(secret in text.lower() for secret in ("raw_provider_payload", "private_body", "credential", "access_token", "refresh_token", "token=", "secret=", "credential=")):
                continue
            if key == "original_url":
                # The archive layer cannot trust arbitrary media rows from JSONL;
                # preserve canonical_url and drop original_url unless it was already
                # sanitized upstream with no query secret/tracking values.
                continue
            if key == "canonical_url":
                canonical = _safe_youtube_canonical_url(text, str(value.get("video_id") or ""))
                if not canonical:
                    continue
                out[key] = canonical
                continue
            out[key] = text[:500 if key.endswith("url") else 180]
    if out.get("type") != "video" or out.get("platform") != "youtube" or not out.get("video_id"):
        return {}
    return out


def _safe_youtube_canonical_url(url: str, video_id_hint: str = "") -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        parsed = None
    video_id = ""
    if parsed is not None:
        host = (parsed.hostname or "").lower()
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        parts = [part for part in parsed.path.split("/") if part]
        if host.endswith("youtu.be") and parts:
            video_id = parts[0]
        elif parsed.path == "/watch":
            video_id = query.get("v", "")
        elif len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            video_id = parts[1]
    video_id = _clean_text(video_id or video_id_hint)[:40]
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        return ""
    return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"


def _video_provenance_line(item: Mapping[str, object]) -> str:
    media = _sanitize_media(item.get("media"))
    content_analysis = _sanitize_content_analysis(item.get("content_analysis"))
    if not media and not content_analysis:
        return ""
    tier = str(content_analysis.get("evidence_tier") or media.get("analysis_provenance") or media.get("metadata_provenance") or "metadata_only")
    tier = _normalize_evidence_tier(tier)
    status = str(content_analysis.get("analysis_status") or media.get("analysis_status") or "unknown")
    channel = str(media.get("channel_title") or "unknown")
    labels = {
        "metadata_only": "YouTube Data API 메타데이터 기반 · 공개 메타데이터 기준",
        "operator_note": "운영자 메모 기준",
        "model_public_youtube_av_no_raw": "모델 기반 YouTube URI 분석 · 모델 기반 공개 YouTube AV 분석 · 공식 caption/transcript 근거 아님 · 자막/transcript 근거 아님",
        "official_caption_ephemeral": "공식 caption 기반 요약",
        "official_caption_unavailable": "공식 caption 분석 불가",
    }
    label = labels.get(tier, f"video provenance `{tier}`")
    provenance = content_analysis.get("analysis_provenance") or media.get("analysis_provenance") or media.get("metadata_provenance") or tier
    return f"  - 영상 근거: {label} · tier=`{tier}` · provenance=`{_safe_title(str(provenance))}` · status=`{status}` · channel=`{_safe_title(channel)}`"


def _topic_overview(items: list[dict[str, str]], *, limit: int = 8) -> str:
    return " · ".join(f"{topic} {len(topic_items)}" for topic, topic_items in group_items_by_topic(items)[:limit])


def _source_link_line(title: str, url: str) -> str:
    if not url:
        return "  - 출처 링크: 메일 본문 내 공개 외부 링크 없음"
    return f"  - 출처 링크: [{title}]({url})"


def _compact_source_label(item: dict[str, str]) -> str:
    sender = _safe_title(item.get("sender") or "unknown")
    received = _clean_text(item.get("received_at") or "unknown")
    kind = _clean_text(item.get("kind") or "post")
    return f"{sender} · {received} · `{kind}`"


def _save_point(item: dict[str, str], classification: TopicClassification, summary: list[str]) -> str:
    title = _clean_text(item.get("article_title") or item.get("title") or "이 항목")
    if classification.primary == "other_tech_report":
        return f"저장 포인트: `{title[:70]}`의 공개 원문 근거를 다음 수집에서 재확인"
    technical_hint = ", ".join(classification.reasons[:2]) or classification.primary_display
    return f"저장 포인트: {technical_hint} 변화가 {summary[2][:110]}"


def group_items_by_topic(items: list[dict[str, str]]) -> list[tuple[str, list[dict[str, str]]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for item in items:
        grouped.setdefault(classify_topic(item), []).append(item)
    topic_priority = {rule.label: rule.priority for rule in _TOPIC_RULES}
    topic_priority.update({"논문/리서치": 900, "기타 테크 리포트": 1000})
    return sorted(grouped.items(), key=lambda pair: (topic_priority.get(pair[0], 999), -len(pair[1]), pair[0]))


def _cardnews_hook(topic: str, topic_items: list[dict[str, str]]) -> str:
    first = topic_items[0] if topic_items else {}
    title = _clean_text(str(first.get("article_title") or first.get("title") or topic))
    if title and title != topic:
        return f"{topic}에서 '{title}' 흐름을 먼저 확인할 때입니다."
    return f"{topic} 흐름을 카드 단위로 빠르게 점검할 때입니다."


def _representative_item(items: list[dict[str, str]]) -> dict[str, str]:
    for item in items:
        if item.get("url"):
            return item
    return items[0] if items else {}


def _blog_intro_lines(*, run_date: str, items: list[dict[str, str]], source_name: str) -> list[str]:
    """Render the article-like opening using only public/sanitized fields."""
    primary = _representative_item(items)
    if not items:
        return [
            "**집현전-Claw 기술 블로그 브리핑**",
            f"작성일: `{run_date}`",
            f"수집 경로: {source_name}",
            "개인정보 경계: 메일 본문/비밀값은 저장·게시하지 않고 공개 아티클 근거와 출처 링크만 사용",
            "",
            "대표 이미지 설명: 비어 있는 수집 보드 앞에서 공개 링크 입력 경로를 점검하는 데이터 편집 데스크",
            "",
            "> 3줄 요약",
            "> 1. 오늘 공개 근거 기반 후보가 수집되지 않았습니다.",
            "> 2. 발행 품질보다 allowlist·쿼리·공개 URL 추출 경로 점검이 우선입니다.",
            "> 3. 다음 실행에서 수집 조건과 원문 접근 가능성을 확인해야 합니다.",
            "",
            "## 왜 지금 이 이슈인가",
            "- 수집 공백은 자동 발행 파이프라인의 입력 품질을 점검해야 한다는 신호입니다.",
            "",
            "## 핵심 주장",
            "- 주장: 공개 근거가 없으면 블로그형 해석보다 수집 조건 검증을 먼저 해야 합니다.",
            "- 근거: 현재 렌더링 가능한 공개 링크가 없습니다.",
            "",
            "## 논증 구조",
            "1. 관찰: 수집 후보가 없습니다.",
            "2. 메커니즘: allowlist, 검색 쿼리, 공개 URL 필터 중 하나가 후보를 제한했을 수 있습니다.",
            "3. 긴장: 자동 발행 속도와 근거 품질 사이의 균형이 필요합니다.",
            "4. 반론: 단기 수집 공백일 수 있어 설정 변경은 신중해야 합니다.",
            "5. 판단: 다음 실행 전 입력 경로를 점검합니다.",
            "",
            "## 산업사회학적·현장기반 해석",
            "- 자동 브리핑은 생성 모델보다 수집·검증·게시 권한의 조직적 경계가 품질을 좌우합니다.",
            "",
            "## 앞으로 볼 질문",
            "- 어떤 수집 조건이 공개 근거 후보를 가장 많이 누락시키는가?",
            "",
            "## 출처",
        ]

    classification = classify_topic_result(primary)
    summary = item_summary_lines(primary)
    title = _safe_title(primary.get("article_title") or primary.get("title") or "대표 후보")
    topic_overview = _topic_overview(items) or classification.primary_display
    return [
        "**집현전-Claw 기술 블로그 브리핑**",
        f"작성일: `{run_date}`",
        f"수집 경로: {source_name}",
        "개인정보 경계: 메일 본문/비밀값은 저장·게시하지 않고 공개 아티클 근거와 출처 링크만 사용",
        "",
        f"대표 이미지 설명: {classification.primary_display} 흐름을 데이터 흐름, 연구 노트, 현장 의사결정 보드로 은유한 추상 일러스트",
        "",
        "> 3줄 요약",
        f"> 1. {summary[0]}",
        f"> 2. {summary[1]}",
        f"> 3. {summary[2]}",
        "",
        "## 왜 지금 이 이슈인가",
        f"- {topic_overview} 흐름을 단순 링크 목록이 아니라 하나의 기술 변화 문제로 묶어 읽습니다.",
        f"- 대표 후보 `{title}`의 방법·평가·적용 조건을 공개 원문 기준으로 확인합니다.",
        "",
        "## 핵심 주장",
        f"- 주장: 오늘의 브리핑은 {classification.primary_display} 변화가 연구·제품·운영 현장에 어떤 선택 비용을 만드는지 소개합니다.",
        f"- 근거: {summary[2]}",
        "- 현장 사례 또는 적용 장면: 연구자·운영자가 원문 방법과 평가 조건을 확인한 뒤 도입 여부를 판단합니다.",
        "",
        "## 논증 구조",
        f"1. 관찰: {summary[0]}",
        f"2. 메커니즘: {summary[1]}",
        "3. 긴장: 빠른 자동 발행과 공개 근거 검증 비용 사이의 균형이 필요합니다.",
        "4. 반론: 공개 요약만으로는 원문의 한계와 적용 조건을 일반화하기 어렵습니다.",
        "5. 판단: 출처가 확인된 항목부터 좁게 읽고, 운영 적용 전 원문을 확인합니다.",
        "",
        "## 산업사회학적·현장기반 해석",
        "- 기술 브리핑의 품질은 모델 성능만이 아니라 조직의 수집·검증·공유 루틴에 좌우됩니다.",
        "- 누가 도입 이익을 얻고 누가 검증·비용 부담을 지는지가 후속 판단의 핵심입니다.",
        "",
        "## 앞으로 볼 질문",
        "- 원문이 제시한 방법·평가 조건은 실제 운영 환경에서도 유지되는가?",
        "- 공개 근거만으로 판단할 수 없는 비용과 리스크는 무엇인가?",
        "",
        "## 출처",
    ]


def _blog_post_contract_lines(*, items: list[dict[str, str]]) -> list[str]:
    """Render the blog-style publication contract from public/sanitized fields only."""
    if not items:
        return [
            "",
            "## 블로그 포스팅 구조",
            "",
            "![대표 이미지 설명: 비어 있는 수집 보드 앞에서 공개 링크 입력 경로를 점검하는 데이터 편집 데스크](이미지_프롬프트)",
            "",
            "> 3줄 요약",
            "> 1. 오늘 공개 근거 기반 후보가 수집되지 않았습니다.",
            "> 2. 발행 품질보다 allowlist·쿼리·공개 URL 추출 경로 점검이 우선입니다.",
            "> 3. 다음 실행에서 수집 조건과 원문 접근 가능성을 확인해야 합니다.",
            "",
            "## 왜 지금 이 이슈인가",
            "- 수집 공백은 자동 발행 파이프라인의 입력 품질을 점검해야 한다는 신호입니다.",
            "",
            "## 핵심 주장",
            "- 주장: 공개 근거가 없으면 블로그형 해석보다 수집 조건 검증을 먼저 해야 합니다.",
            "- 근거: 현재 렌더링 가능한 공개 링크가 없습니다.",
            "- 현장 사례 또는 적용 장면: Discord 발행 전 raw archive 생성 상태를 확인합니다.",
            "",
            "## 논증 구조",
            "1. 관찰: 수집 후보가 없습니다.",
            "2. 메커니즘: allowlist, 검색 쿼리, 공개 URL 필터 중 하나가 후보를 제한했을 수 있습니다.",
            "3. 긴장: 자동 발행 속도와 근거 품질 사이의 균형이 필요합니다.",
            "4. 반론: 단기 수집 공백일 수 있어 설정 변경은 신중해야 합니다.",
            "5. 판단: 다음 실행 전 입력 경로를 점검합니다.",
            "",
            "## 산업사회학적·현장기반 해석",
            "- 자동 브리핑은 생성 모델보다 수집·검증·게시 권한의 조직적 경계가 품질을 좌우합니다.",
            "",
            "## 앞으로 볼 질문",
            "- 어떤 수집 조건이 공개 근거 후보를 가장 많이 누락시키는가?",
            "",
            "## 카드뉴스 재사용안",
            "1. 카드 1: 오늘은 공개 근거 후보가 없습니다.",
            "2. 카드 2: 수집 조건을 먼저 점검합니다.",
            "3. 카드 3: 원문 링크와 raw archive 생성을 확인합니다.",
            "",
            "## 디스코드 브리핑 재사용안",
            "- 한 줄 제목: 공개 근거 후보 없음",
            "- 3줄 요약: 수집 공백 / 설정 점검 / 다음 실행 확인",
            "- 핵심 링크: 없음",
            "- 토론 질문: 어떤 수집 조건을 조정해야 하는가?",
            "",
            "## 출처",
            "- 공개 출처 없음 — 수집 후보 없음",
        ]

    primary = _representative_item(items)
    title = _safe_title(primary.get("article_title") or primary.get("title") or "대표 후보")
    url = primary.get("url") or ""
    classification = classify_topic_result(primary)
    summary = item_summary_lines(primary)
    topic_overview = _topic_overview(items) or classification.primary_display
    source_line = f"[{title}]({url})" if url else "공개 외부 링크 없음"
    return [
        "",
        "## 블로그 포스팅 구조",
        "",
        f"![대표 이미지 설명: {classification.primary_display} 흐름을 공개 근거 보드와 현장 의사결정 테이블로 함께 보여주는 장면](이미지_프롬프트)",
        "",
        "> 3줄 요약",
        f"> 1. {summary[0]}",
        f"> 2. {summary[1]}",
        f"> 3. {summary[2]}",
        "",
        "## 왜 지금 이 이슈인가",
        f"- {classification.primary_display} 관련 공개 후보 {len(items)}개가 같은 발행 묶음에 들어왔습니다.",
        f"- 토픽 인덱스: {topic_overview}",
        "",
        "## 핵심 주장",
        f"- 주장: `{title}`는 {classification.primary_display} 흐름에서 후속 검토할 변화 신호입니다.",
        f"- 근거: {summary[2]}",
        "- 현장 사례 또는 적용 장면: 연구자·운영자가 원문의 방법, 평가 조건, 적용 범위를 확인해야 합니다.",
        "",
        "## 논증 구조",
        f"1. 관찰: {summary[0]}",
        f"2. 메커니즘: {summary[1]}",
        "3. 긴장: 자동 수집은 속도를 높이지만 공개 근거와 private context 경계를 분리해야 합니다.",
        "4. 반론: 공개 요약만으로는 원문의 한계와 평가 조건을 충분히 판단하기 어렵습니다.",
        f"5. 판단: {classification.primary_display} 축의 후속 읽기 후보로 저장하되 원문 확인을 전제로 봅니다.",
        "",
        "## 산업사회학적·현장기반 해석",
        "- 이 브리핑은 모델 성능보다 조직의 수집·검증·공유 루틴이 지식 확산 속도를 어떻게 바꾸는지 보여줍니다.",
        "- 누가 검증 비용을 부담하고 어떤 팀이 원문 해석을 운영 의사결정으로 연결하는지가 핵심입니다.",
        "",
        "## 앞으로 볼 질문",
        "- 원문이 제시한 방법과 평가 지표가 실제 운영 환경에서도 유지되는가?",
        "- 공개 근거만으로 판단할 수 없는 비용·리스크·적용 조건은 무엇인가?",
        "",
        "## 카드뉴스 재사용안",
        f"1. 카드 1: {classification.primary_display}에서 지금 볼 변화",
        f"2. 카드 2: {summary[0]}",
        f"3. 카드 3: {summary[1]}",
        "4. 카드 4: 현장의 쟁점은 검증 비용과 적용 조건",
        f"5. 카드 5: 남는 질문과 출처 — {source_line}",
        "",
        "## 디스코드 브리핑 재사용안",
        f"- 한 줄 제목: {title}",
        f"- 3줄 요약: {summary[0]} / {summary[1]} / {summary[2]}",
        f"- 핵심 링크: {source_line}",
        "- 토론 질문: 이 변화가 우리 운영 환경에서도 같은 효과를 내는가?",
        "",
        "## 출처",
        f"- {source_line} — 공개 원문/요약 기반 핵심 후보",
    ]


def render_topic_briefing(
    *,
    run_date: str,
    items: list[dict[str, str]],
    source_name: str,
    max_items_per_topic: int = 3,
) -> str:
    """Render a Discord-ready Markdown carousel/cardnews briefing.

    The contract mirrors the Apps Script relay path: compact cards with a
    hook/context/change/why/evidence/implication/CTA arc, while preserving the
    privacy boundary that private email bodies never appear in persisted output.
    """
    lines = _blog_intro_lines(run_date=run_date, items=items, source_name=source_name)
    for item in items[:12]:
        title = _safe_title(item.get("article_title") or item.get("title") or "(untitled newsletter item)")
        url = item.get("url") or ""
        if url:
            lines.append(f"- [{title}]({url}) — 공개 원문/요약 근거")
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "## 카드뉴스 발행 템플릿",
        "",
        f"- 수집 항목: {len(items)}개",
        "- 구성: 훅 → 맥락 → 핵심 변화 → 왜 중요한가 → 근거 → 시사점 → CTA/저장 포인트",
        "- 기준: 허용된 수집 경로의 메타데이터와 공개 아티클 원문/요약만 사용",
        "- 플랫폼 메모: Discord Markdown에서도 카드 단위로 읽히도록 compact하게 렌더링",
    ]
    lines += _blog_post_contract_lines(items=items)
    if not items:
        lines += [
            "",
            "### 카드 0. 수집 결과 없음",
            "- 훅: 오늘 카드뉴스로 전환할 공개 기술 후보가 없습니다.",
            "- 맥락: 설정된 allowlist와 연구/테크 URL 조건에 맞는 항목이 없습니다.",
            "- 핵심 변화: 신규 후보가 없어 토픽 변화 신호를 산출하지 않았습니다.",
            "- 왜 중요한가: 수집 공백은 발행 품질보다 입력 경로 점검이 우선이라는 신호입니다.",
            "- 근거: sender_allowlist, export 경로, max_source_bytes, URL host hint를 점검해야 합니다.",
            "- 시사점: 다음 실행 전 수집 조건과 공개 링크 추출 상태를 확인하세요.",
            "- CTA/저장 포인트: 설정을 고친 뒤 다시 발행하고 raw archive 생성을 확인하세요.",
            "- 출처 링크: 없음",
        ]
        return "\n".join(lines) + "\n"

    overview = _topic_overview(items)
    if overview:
        lines += ["", f"토픽 인덱스: {overview}"]

    card_number = 0
    for topic, topic_items in group_items_by_topic(items):
        card_number += 1
        lines += ["", "━━━━━━━━━━━━━━━━━━━━", f"### 카드 {card_number}. {topic}", f"- 훅: {_cardnews_hook(topic, topic_items)}", f"- 맥락: 공개 근거 {len(topic_items)}개를 같은 변화 축으로 묶었습니다."]
        for item_index, item in enumerate(topic_items[:max_items_per_topic], start=1):
            title = _safe_title(item.get("article_title") or item.get("title") or "(untitled newsletter item)")
            url = item.get("url") or ""
            classification = classify_topic_result(item)
            evidence = ", ".join(classification.reasons) or "fallback"
            tags = ", ".join(classification.secondary) or "none"
            summary = item_summary_lines(item)
            lines += [
                "",
                f"**{card_number}.{item_index} {title}**",
                f"  - 핵심 변화: {summary[0]}",
                f"  - 왜 중요한가: {summary[1]}",
                f"  - 근거: {summary[2]}",
                f"  - 시사점: primary=`{classification.primary}` · tags=`{tags}` · confidence={classification.confidence:.2f} · 근거 `{evidence}`",
                f"  - CTA/저장 포인트: {_save_point(item, classification, summary)}",
                _source_link_line(title, url),
                f"  - 수집 메타: {_compact_source_label(item)}",
            ]
            video_note = _video_provenance_line(item)
            if video_note:
                lines.append(video_note)
        remaining = len(topic_items) - max_items_per_topic
        if remaining > 0:
            lines.append(f"- raw archive 추가 보존: {remaining}개")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "운영 메모: 카드뉴스는 Discord Markdown/캐러셀 초안용 구조이며, 링크 임베드 미리보기는 억제하고 private email context는 출력하지 않습니다.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Local Gmail Takeout .mbox or sanitized .jsonl export")
    parser.add_argument("--wiki-root", required=True, help="PaperWiki/PaperWiki root")
    parser.add_argument("--date", default=_date.today().isoformat(), help="Run date folder/page date")
    parser.add_argument(
        "--sender-allowlist",
        default="",
        help="Comma-separated sender/domain substrings to include",
    )
    parser.add_argument(
        "--allow-all-senders",
        action="store_true",
        help="Explicitly process all messages in the local export; use only with sanitized exports",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=_DEFAULT_MAX_MESSAGES,
        help=f"Maximum messages to inspect from the export (default: {_DEFAULT_MAX_MESSAGES})",
    )
    parser.add_argument(
        "--max-source-bytes",
        type=int,
        default=25 * 1024 * 1024,
        help="Maximum export file size to read (default: 25 MiB); set 0 to disable",
    )
    parser.add_argument(
        "--include-all-urls",
        action="store_true",
        help="Include all extracted URLs instead of research/post host hints only",
    )
    parser.add_argument(
        "--briefing-path",
        help="Optional Markdown path for a Discord-ready topic briefing",
    )
    parser.add_argument(
        "--max-items-per-topic",
        type=int,
        default=3,
        help="Maximum items rendered under each topic in --briefing-path output",
    )
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser()
    wiki_root = Path(args.wiki_root).expanduser()
    if not source.exists():
        print(f"source export not found: {source}", file=sys.stderr)
        return 1
    allow = [s.strip() for s in args.sender_allowlist.split(",") if s.strip()]
    if not allow and not args.allow_all_senders:
        print(
            "newsletter ingest requires --sender-allowlist or explicit --allow-all-senders",
            file=sys.stderr,
        )
        return 2
    if args.max_messages < 1:
        print("--max-messages must be >= 1", file=sys.stderr)
        return 2
    max_source_bytes = None if args.max_source_bytes == 0 else args.max_source_bytes
    try:
        enforce_source_size(source, max_source_bytes=max_source_bytes)
        messages = load_messages(source, max_messages=args.max_messages)
        items = select_items(messages, sender_allowlist=allow, include_all_urls=args.include_all_urls)
        raw_path, page_path = publish_items(
            wiki_root=wiki_root,
            run_date=args.date,
            source_path=source,
            items=items,
        )
        if args.briefing_path:
            briefing_path = Path(args.briefing_path).expanduser()
            briefing_path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(
                briefing_path,
                render_topic_briefing(
                    run_date=args.date,
                    items=items,
                    source_name=source.name,
                    max_items_per_topic=args.max_items_per_topic,
                ),
            )
    except Exception as exc:
        print(f"newsletter ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {raw_path}")
    print(f"wrote {page_path}")
    if args.briefing_path:
        print(f"wrote {Path(args.briefing_path).expanduser()}")
    print(f"items: {len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
