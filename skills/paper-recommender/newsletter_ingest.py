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
from typing import Iterable, Iterator


_URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:!?)]}>'\""

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

_DEFAULT_MAX_MESSAGES = 500

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
    blocked = (
        "myaccount.google.com",
        "accounts.google.com",
        "mail.google.com",
        "support.google.com",
        "google.com/analytics/answer",
        "unsubscribe",
        "preferences",
        "privacy",
        "terms",
        "login",
        "signin",
        "signup",
        "account",
        "settings",
    )
    return any(token in lower for token in blocked)


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
        for url in extract_urls(msg.body):
            if is_private_utility_url(url):
                continue
            if not include_all_urls and not is_research_url(url):
                continue
            key = (msg.subject, url)
            if key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": msg.subject or "(untitled newsletter item)",
                    "url": url,
                    "kind": classify_url(url),
                    "sender": msg.sender,
                    "received_at": msg.received_at,
                }
            )
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
        "> Generated from a user-provided local export. Full email bodies and credentials are not stored in this page.",
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
    }
    out: dict[str, object] = {key: value for key, value in item.items() if key in allowed}
    classification = classify_topic_result(item)
    out.setdefault("primary_topic", classification.primary)
    out.setdefault("primary_topic_display", classification.primary_display)
    out.setdefault("secondary_topics", list(classification.secondary))
    out.setdefault("topic_confidence", classification.confidence)
    out.setdefault("topic_reasons", list(classification.reasons))
    out["topic_context"] = analyze_topic_context(item, mode="shadow")
    return out


def item_summary_lines(item: dict[str, object]) -> list[str]:
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


def group_items_by_topic(items: list[dict[str, str]]) -> list[tuple[str, list[dict[str, str]]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for item in items:
        grouped.setdefault(classify_topic(item), []).append(item)
    topic_priority = {rule.label: rule.priority for rule in _TOPIC_RULES}
    topic_priority.update({"논문/리서치": 900, "기타 테크 리포트": 1000})
    return sorted(grouped.items(), key=lambda pair: (topic_priority.get(pair[0], 999), -len(pair[1]), pair[0]))


def render_topic_briefing(
    *,
    run_date: str,
    items: list[dict[str, str]],
    source_name: str,
    max_items_per_topic: int = 3,
) -> str:
    lines = [
        "**집현전-Claw 뉴스레터 수집 브리핑**",
        f"_date: {run_date}_",
        f"_source: {source_name}_",
        "_privacy: 메일 본문/개인정보는 게시하지 않고 메타데이터와 추출 URL만 사용_",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "## 토픽별 기술 리포트/뉴스레터 요약",
        "",
        f"- 수집 항목: {len(items)}개",
        "- 기준: 허용된 수집 경로에서 메일 메타데이터와 공개 아티클 원문/요약만 받아 토픽별 정리",
        "- 운영 메모: 메일 본문/개인정보는 저장하지 않고 공개 URL의 기술 근거와 출처 링크만 게시",
    ]
    if not items:
        lines += [
            "",
            "### 수집 결과 없음",
            "- 핵심 요약: 설정된 allowlist와 연구/테크 URL 조건에 맞는 항목이 없습니다.",
            "- 기술 포인트: sender_allowlist, export 경로, max_source_bytes, URL host hint를 점검해야 합니다.",
            "- 출처 링크: 없음",
        ]
        return "\n".join(lines) + "\n"

    for topic, topic_items in group_items_by_topic(items):
        lines += ["", f"### {topic}"]
        for item in topic_items[:max_items_per_topic]:
            title = _safe_title(item.get("article_title") or item.get("title") or "(untitled newsletter item)")
            sender = _safe_title(item.get("sender") or "unknown")
            kind = item.get("kind") or "post"
            received = item.get("received_at") or "unknown"
            url = item.get("url") or ""
            classification = classify_topic_result(item)
            evidence = ", ".join(classification.reasons) or "fallback"
            tags = ", ".join(classification.secondary) or "none"
            summary = item_summary_lines(item)
            lines += [
                f"- 주요 아티클/논문: {title}",
                f"  - 핵심 요약: {summary[0]}",
                f"  - 기술 포인트: {summary[1]}",
                f"  - 의미/근거: {summary[2]}",
                f"  - 분류 근거: `{kind}` 유형, primary=`{classification.primary}`, tags=`{tags}`, confidence={classification.confidence:.2f}, 토픽 근거 `{evidence}`. 발신자 `{sender}`, 수신일 `{received}`",
                f"- 출처 링크: [{title}]({url})",
            ]
        remaining = len(topic_items) - max_items_per_topic
        if remaining > 0:
            lines.append(f"- 추가 항목: {remaining}개는 raw archive에 보존")

    lines += ["", "━━━━━━━━━━━━━━━━━━━━", "원문 메일 본문은 게시하지 않고 raw archive에는 추출 URL/메타데이터만 보존됩니다."]
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
