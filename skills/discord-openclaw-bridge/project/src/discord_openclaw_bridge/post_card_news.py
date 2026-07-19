from __future__ import annotations

import asyncio
import hashlib
import html
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .openclaw_gateway import (
    OpenClawGatewayClient,
    OpenClawGatewayPolicy,
    is_loopback_base_url,
)
from .post_newsletter import (
    DISCORD_SUPPRESS_EMBEDS_FLAG,
    NewsletterPostConfigError,
    _delete_message_with_rate_limit,
    _load_dotenv,
    _post_message_with_rate_limit,
    _required_snowflake,
)
from .config import _env_flag
from .miner import _SENSITIVE_QUERY_KEYS, _TRACKING_QUERY_KEYS, clean_text, sanitize_url
from .publication_trust_gate import PublicationTrustGateError, run_publication_trust_gate

DEFAULT_CARD_NEWS_CHANNEL_ID = ""
DEFAULT_OPS_REPORT_CHANNEL_ID = "1502980129343672504"  # 운영리포팅 forum
CARD_NEWS_TITLE = "집현전-Claw 카드뉴스"
FORUM_CHANNEL_TYPES = {15}
CARD_NEWS_THREAD_NAME_MARKERS = (
    "기술 브리핑 카드뉴스",
    "블로그 포스팅 워크플로우 카드뉴스",
)

CARD_SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
GENERIC_TOPIC = "기타 테크 리포트"

_GRAPH_EMBEDDING_FAMILY_RE = re.compile(
    r"\b(?:dynamic|temporal|heterogeneous|multiplex|evolving)\s+(?:graph|network)\s+"
    r"(?:embedding|representation(?:\s+learning)?)\b"
    r"|\b(?:graph|network)\s+representation\s+learning\b",
    re.IGNORECASE,
)
LEAN_DISCLAIMER_WITH_EXCERPT = "확인 한계: 위 문장은 공개 페이지 요약/초록 기준입니다. 세부 실험 조건은 원문에서 확인해야 합니다."
LEAN_DISCLAIMER_WITHOUT_EXCERPT = "확인 한계: 공개 요약/초록을 확보하지 못했습니다. 제목만으로 결론을 내리지 않습니다."
CONNECTIVES = ("따라서", "다만", "구체적으로", "한편")
TOPIC_PRIORITY: dict[str, int] = {
    "검색/RAG/지식그래프": 10,
    "LLM/에이전트": 20,
    "멀티모달/비전": 30,
    "인프라/배포": 40,
    "오픈소스/코드": 50,
    "AI 안전/평가": 60,
    "산업/제품 동향": 70,
    "논문/리서치": 80,
    GENERIC_TOPIC: 900,
}
CARD_NEWS_DUPLICATE_SKIP_REASONS = {"min_new_cards", "max_previous_overlap_ratio"}
_REGISTER_PAIRS = (
    # Order matters: longer / more specific endings first so they win the lookahead match.
    ("되었다", "되었습니다"),
    ("하였다", "하였습니다"),
    ("했다", "했습니다"),
    ("였다", "였습니다"),
    ("많았다", "많았습니다"),
    ("좋았다", "좋았습니다"),
    ("작았다", "작았습니다"),
    ("높았다", "높았습니다"),
    ("낮았다", "낮았습니다"),
    ("컸다", "컸습니다"),
    ("된다", "됩니다"),
    ("한다", "합니다"),
    ("하다", "합니다"),
    ("는다", "습니다"),
    ("이다", "입니다"),
    ("있다", "있습니다"),
    ("없다", "없습니다"),
    ("많다", "많습니다"),
    ("좋다", "좋습니다"),
    ("작다", "작습니다"),
    ("높다", "높습니다"),
    ("낮다", "낮습니다"),
    ("크다", "큽니다"),
)
_ACTION_SIGNAL_TERMS = (
    "원문",
    "확인",
    "검증",
    "검토",
    "도입",
    "운영",
    "평가",
    "재현",
    "한계",
    "주의",
    "리스크",
    "트레이드오프",
    "trade-off",
    "trade off",
)
_INTERROGATIVE_SUFFIXES = (
    "인가",
    "는가",
    "할 수 있는가",
    "해야 하는가",
    "가능한가",
)
_QUESTION_TRANSFORMS = (
    (re.compile(r"^(.+?)을 함께 제시합니다\.?$"), r"\1이 같은 조건에서 비교 가능한가?"),
    (re.compile(r"^(.+?)를 함께 제시합니다\.?$"), r"\1가 같은 조건에서 비교 가능한가?"),
    (re.compile(r"^(.+?)을 함께 측정합니다\.?$"), r"\1이 운영 환경에서도 유지되는가?"),
    (re.compile(r"^(.+?)를 함께 측정합니다\.?$"), r"\1가 운영 환경에서도 유지되는가?"),
    (re.compile(r"^(.+?)을 측정합니다\.?$"), r"\1이 운영 환경에서도 유지되는가?"),
    (re.compile(r"^(.+?)를 측정합니다\.?$"), r"\1가 운영 환경에서도 유지되는가?"),
)
_META_DESC_PATTERN = re.compile(
    r"<meta\b(?=[^>]*(?:name|property)\s*=\s*['\"](?:description|og:description|twitter:description)['\"])[^>]*\bcontent\s*=\s*(['\"])(.*?)\1[^>]*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TITLE_PATTERN = re.compile(r"<title\b[^>]*>(.*?)</title>", flags=re.IGNORECASE | re.DOTALL)
_ARXIV_ABSTRACT_PATTERN = re.compile(
    r"<blockquote\b[^>]*class\s*=\s*['\"][^'\"]*\babstract\b[^'\"]*['\"][^>]*>(.*?)</blockquote>",
    flags=re.IGNORECASE | re.DOTALL,
)
_TAG_PATTERN = re.compile(r"<[^>]+>")
_SKIP_FETCH_URL_PATTERNS = (
    "linkedin.com/comm/",
    "linkedin.com/feed",
    "linkedin.com/mynetwork",
    "linkedin.com/messaging",
    "cardreceipt",
    "unsubscribe",
    "preferences",
)
_NON_ARTICLE_TERMS = (
    "digest",
    "receipt",
    "invoice",
    "order",
    "pedido",
    "pix",
    "weekly",
    "analytics",
    "access request",
    "액세스 권한",
    "제안을 보냈습니다",
    "답장을 기다리고",
    "주차",
    "unsubscribe",
    "preferences",
)
_DIGEST_TITLE_TERMS = (
    "digest",
    "weekly",
    "뉴스레터",
    "오마카세",
    "주차",
)
_TECH_RELEVANCE_TERMS = (
    "ai",
    "agent",
    "anthropic",
    "benchmark",
    "claude",
    "code",
    "csail",
    "data",
    "eval",
    "graph",
    "knowledge",
    "llm",
    "machine learning",
    "model",
    "multimodal",
    "paper",
    "rag",
    "research",
    "retrieval",
    "vision",
    "검색",
    "논문",
    "모델",
    "멀티모달",
    "에이전트",
    "지식그래프",
)


@dataclass(frozen=True)
class CardNewsQualityGateConfig:
    enabled: bool = True
    audit_path: Path | None = None
    history_days: int = 14
    min_publishable_cards: int = 3
    min_new_cards: int = 3
    max_previous_overlap_ratio: float = 0.5
    min_evidence_cards: int = 2
    content_similarity_threshold: float = 0.72
    agent_dedupe_enabled: bool = False
    agent_dedupe_max_previous: int = 5
    agent_dedupe_timeout_sec: float = 45.0


def _strip_emoji(value: str) -> str:
    return "".join(
        char
        for char in value
        if not (
            "\U0001F000" <= char <= "\U0001FAFF"
            or "☀" <= char <= "➿"
        )
    ).strip()


def _clean_title(value: object, *, limit: int | None = None) -> str:
    return clean_text(_strip_emoji(str(value or "")), limit=limit)


def _clean_multiline(value: object) -> str:
    lines = [clean_text(line) for line in str(value or "").splitlines()]
    return "\n".join(line for line in lines if line)


def _latest_archive_path() -> Path:
    root = Path(os.environ.get("NEWSLETTER_WIKI_ROOT", str(Path.home() / ".openclaw" / "workspace" / "wiki"))).expanduser()
    raw_root = root / "raw" / "newsletters"
    today_path = raw_root / date.today().isoformat() / "items.json"
    if today_path.exists():
        return today_path
    candidates = sorted(raw_root.glob("*/items.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    raise NewsletterPostConfigError(f"newsletter raw archive not found under {raw_root}")


def _load_archive(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise NewsletterPostConfigError(f"card news archive source not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise NewsletterPostConfigError(f"invalid newsletter archive payload: {path}")
    return payload


def _summary_lines(item: dict[str, Any]) -> list[str]:
    raw = item.get("summary_lines") or item.get("summaryLines") or []
    lines: list[str] = []
    if isinstance(raw, list):
        for line in raw:
            text = clean_text(line, limit=160)
            if text and text not in lines:
                lines.append(text)
            if len(lines) == 3:
                break
    return lines[:3]


def _source_name(item: dict[str, Any]) -> str:
    return clean_text(
        item.get("source_name")
        or item.get("sender_name")
        or item.get("newsletter_name")
        or item.get("sender")
        or "원문",
        limit=80,
    )


def _title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "Untitled", limit=90)


def _raw_title(item: dict[str, Any]) -> str:
    return _clean_title(item.get("article_title") or item.get("title") or "Untitled")


_HANGUL_BASE = 0xAC00
_HANGUL_LAST = 0xD7A3
_JONGSEONG_NIEUN = 4  # ㄴ
_JONGSEONG_SSANG_SIOT = 20  # ㅆ (past tense marker like 갔/했/었)
_JONGSEONG_BIEUP = 17  # ㅂ
_DA_PATTERN = re.compile(r"([가-힣])다(?=[.!?]|$)")


def _convert_da_ending(match: re.Match[str]) -> str:
    char = match.group(1)
    code = ord(char) - _HANGUL_BASE
    final = code % 28
    if final == _JONGSEONG_NIEUN:
        # (vowel-stem)ㄴ다 → (stem with ㅂ final)니다 (e.g. 인 → 입, 룬 → 룹)
        swapped = chr(_HANGUL_BASE + code - _JONGSEONG_NIEUN + _JONGSEONG_BIEUP)
        return f"{swapped}니다"
    if final == _JONGSEONG_SSANG_SIOT:
        # past tense (...)ㅆ다 → (...)ㅆ습니다 (e.g. 났 → 났습니다, 갔 → 갔습니다)
        return f"{char}습니다"
    return match.group(0)


def _normalize_register(text: str) -> str:
    if not text:
        return text
    for src, dst in _REGISTER_PAIRS:
        text = re.sub(rf"{src}(?=[.!?]|$)", dst, text)
    text = _DA_PATTERN.sub(_convert_da_ending, text)
    return text


def _confidence_bucket(value: object) -> str:
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "잠정"
    if v >= 0.7:
        return "높음"
    if v >= 0.5:
        return "보통"
    return "잠정"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _strip_html(value: str) -> str:
    text = html.unescape(value or "")
    text = _TAG_PATTERN.sub(" ", text)
    return clean_text(text)


def _extract_public_metadata(markup: str) -> dict[str, str]:
    description = ""
    for match in _META_DESC_PATTERN.finditer(markup or ""):
        candidate = _strip_html(match.group(2))
        if len(candidate) >= 40 and not candidate.lower().startswith("abstract page for arxiv paper"):
            description = candidate
            break
        if len(candidate) >= 40 and not description:
            description = candidate
    title = ""
    title_match = _TITLE_PATTERN.search(markup or "")
    if title_match:
        title = _strip_html(title_match.group(1))
    if (not description or description.lower().startswith("abstract page for arxiv paper")):
        abstract_match = _ARXIV_ABSTRACT_PATTERN.search(markup or "")
        if abstract_match:
            abstract = _strip_html(abstract_match.group(1))
            abstract = re.sub(r"^Abstract:\s*", "", abstract, flags=re.IGNORECASE).strip()
            if len(abstract) >= 40:
                description = abstract
    return {"description": description, "title": title}


def _should_fetch_public_metadata(url: str) -> bool:
    lower = sanitize_url(url).lower()
    if not lower.startswith(("http://", "https://")):
        return False
    return not any(pattern in lower for pattern in _SKIP_FETCH_URL_PATTERNS)


def _metadata_is_better(item: dict[str, Any], description: str) -> bool:
    if not description:
        return False
    raw_title = _raw_title(item)
    current = _substantive_excerpt(item, raw_title=raw_title)
    if not current:
        return True
    # Public page metadata is preferred over archived newsletter snippets when
    # it gives materially more context, but keep existing curated summaries.
    return len(description) > len(current) + 40


def _looks_like_digest_title(value: object) -> bool:
    title = _clean_title(value).lower()
    return any(term in title for term in _DIGEST_TITLE_TERMS)


async def enrich_public_metadata(
    payload: dict[str, Any],
    client: httpx.AsyncClient,
    *,
    max_items: int = 24,
) -> dict[str, Any]:
    """Fill thin archive items with public page metadata before card selection.

    The newsletter archive can contain only titles/profile tracking links.  We
    do not scrape long article bodies; we only add short public meta
    descriptions/titles from the URL that will be shown as the source link.
    """
    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        return payload
    candidates = [
        item
        for item in _sort_by_quality([item for item in raw_items if isinstance(item, dict)])
        if _should_fetch_public_metadata(clean_text(item.get("url")))
    ][:max_items]
    if not candidates:
        return payload

    async def fetch_one(item: dict[str, Any]) -> None:
        url = sanitize_url(item.get("url"))
        if not url:
            return
        try:
            response = await client.get(
                url,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; OpenClawCardNews/1.0)"},
            )
        except httpx.HTTPError:
            return
        if response.status_code >= 400:
            return
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type and content_type:
            return
        meta = _extract_public_metadata(response.text[:160_000])
        description = clean_text(meta.get("description"), limit=320)
        if _metadata_is_better(item, description):
            item["article_description"] = description
            item["public_excerpt"] = description
            item["metadata_enriched"] = True
        title = _clean_title(meta.get("title"), limit=120)
        current_title = clean_text(item.get("article_title") or item.get("title"))
        if title and (not current_title or _looks_like_digest_title(current_title)):
            item["article_title"] = title

    await asyncio.gather(*(fetch_one(item) for item in candidates))
    return payload


def _format_footer(
    source: str,
    url: str,
    topic: str,
    bucket: str,
    *,
    reasons: list[str] | None = None,
) -> str:
    parts = [f"— {source}", f"<{sanitize_url(url)}>"]
    if topic and topic != GENERIC_TOPIC:
        parts.append(f"`{topic}`")
    parts.append(f"`{bucket}`")
    base = " · ".join(parts)
    if reasons:
        cleaned = [r for r in (clean_text(item, limit=40) for item in reasons[:2]) if r]
        if cleaned:
            label = f"단서 {cleaned[0]}"
            tail = " · ".join([label, *cleaned[1:]])
            base = f"{base} · {tail}"
    return base


def _has_substring_overlap(prev: str, new: str, *, min_len: int = 30) -> bool:
    if len(new) < min_len or len(prev) < min_len:
        return False
    for i in range(len(new) - min_len + 1):
        if new[i:i + min_len] in prev:
            return True
    return False


def _dedup_paragraphs(paragraphs: list[str]) -> list[str]:
    kept: list[str] = []
    for para in paragraphs:
        if any(_has_substring_overlap(prev, para) for prev in kept):
            continue
        kept.append(para)
    return kept


def _richness(item: dict[str, Any], *, raw_title: str) -> str:
    if (
        clean_text(item.get("hook") or item.get("why_now"))
        or clean_text(item.get("core_change") or item.get("claim") or item.get("thesis"))
        or clean_text(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"))
        or clean_text(item.get("why_matters"))
        or clean_text(item.get("evidence"))
        or clean_text(item.get("cta") or item.get("save_point"))
        or _summary_lines(item)
    ):
        return "rich"
    excerpt = clean_text(item.get("public_excerpt") or item.get("article_description"))
    if excerpt and _clean_title(excerpt).lower() != raw_title.lower():
        return "lean"
    return "skeletal"




def _canonical_title_key(item: dict[str, Any]) -> str:
    title = clean_text(item.get("article_title") or item.get("title"), limit=140).lower()
    return re.sub(r"[^0-9a-z가-힣]+", " ", title).strip()


def _visible_signal(text: str) -> str:
    cleaned = _clean_title(text, limit=500)
    cleaned = re.sub(r"[​-‏⁠﻿͏]+", "", cleaned)
    return cleaned.strip()


def _substantive_excerpt(item: dict[str, Any], *, raw_title: str) -> str:
    excerpt = _visible_signal(str(item.get("public_excerpt") or item.get("article_description") or ""))
    if not excerpt:
        return ""
    title_key = _canonical_title_key({"title": raw_title})
    excerpt_key = re.sub(r"[^0-9a-z가-힣]+", " ", excerpt.lower()).strip()
    if not excerpt_key or excerpt_key == title_key:
        return ""
    if len(excerpt_key) < 5:
        return ""
    return excerpt


def _url_quality(url: str) -> int:
    lower = sanitize_url(url).lower().replace("&amp;", "&")
    score = 0
    if any(host in lower for host in ("arxiv.org", "openreview.net", "doi.org", "microsoft.com/en-us/research", "deepmind.google", "openai.com", "anthropic.com", "ai.googleblog.com")):
        score += 8
    if re.search(r"medium\.com/(?:@[^/?]+/[^?]+|[^@?][^?]+/[^?]+)", lower) and not re.search(r"medium\.com/(?:tag|topic|blog/newsletters)(?:[/?]|$)", lower):
        score += 6
    elif re.search(r"medium\.com/(?:@[^/?]+|[^/?]+)(?:[?#]|$)", lower):
        score -= 6
    if any(token in lower for token in ("/publication/", "/research/", "/blog/", "/paper", "/pulse/")):
        score += 3
    if any(token in lower for token in ("/feed", "/messaging", "/mynetwork", "cardreceipt", "sendgrid.net", "unsubscribe", "preferences")):
        score -= 12
    if re.search(r"https?://[^/]+/?(?:[?#].*)?$", lower):
        score -= 10
    if any(token in lower for token in ("midtoken", "midsig", "trktrk", "trkemail", "lipi=", "utm_", "source=email")):
        score -= 2
    return score


def _is_non_article_item(item: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            clean_text(item.get("article_title") or item.get("title")),
            sanitize_url(item.get("url")),
        ]
    ).lower()
    return any(term.lower() in haystack for term in _NON_ARTICLE_TERMS)


def _has_card_evidence(item: dict[str, Any]) -> bool:
    raw_title = _raw_title(item)
    if _summary_lines(item) or _substantive_excerpt(item, raw_title=raw_title):
        return True
    return any(
        clean_text(item.get(field))
        for field in ("hook", "why_now", "core_change", "claim", "thesis", "why_matters", "evidence")
    )


def _is_tech_relevant_item(item: dict[str, Any]) -> bool:
    haystack = " ".join(
        [
            clean_text(item.get("article_title") or item.get("title")),
            clean_text(item.get("source_name") or item.get("sender") or item.get("newsletter_name")),
            clean_text(item.get("primary_topic_display")),
            clean_text(item.get("public_excerpt") or item.get("article_description")),
        ]
    ).lower()
    for term in _TECH_RELEVANCE_TERMS:
        escaped = re.escape(term)
        if re.search(rf"(?<![0-9a-z]){escaped}(?![0-9a-z])", haystack):
            return True
    return False


def _item_quality_score(item: dict[str, Any]) -> int:
    raw_title = _raw_title(item)
    score = _url_quality(clean_text(item.get("url")))
    if _summary_lines(item):
        score += 20
    for field in ("hook", "why_now", "core_change", "claim", "thesis", "why_matters", "evidence"):
        if clean_text(item.get(field)):
            score += 8
    excerpt = _substantive_excerpt(item, raw_title=raw_title)
    if excerpt:
        score += min(14, max(5, len(excerpt) // 60))
    elif clean_text(item.get("public_excerpt") or item.get("article_description")):
        score -= 4
    if clean_text(item.get("primary_topic_display") or GENERIC_TOPIC) == GENERIC_TOPIC:
        score -= 2
    if _is_non_article_item(item):
        score -= 30
    return score


def _story_key(item: dict[str, Any]) -> str:
    title = _raw_title(item).lower()
    snippet = _evidence_snippet(item, limit=220).lower()
    text = title
    quoted_paper = re.search(r"implementation of the paper [\"“](.+?)[\"”]", text)
    if not quoted_paper:
        quoted_paper = re.search(r"implementation of the paper [\"“](.+?)[\"”]", snippet)
    if quoted_paper:
        text = quoted_paper.group(1)
    elif _looks_like_digest_title(title):
        text = f"{title} {snippet}"
    text = re.sub(r"^\[\d{4}\.\d+(?:v\d+)?\]\s*", "", text)
    words = re.findall(r"[0-9a-z가-힣]+", text)
    stop_words = {
        "github",
        "implementation",
        "paper",
        "the",
        "and",
        "for",
        "with",
        "from",
        "newsletter",
        "weekly",
    }
    significant = [word for word in words if len(word) > 1 and word not in stop_words]
    if len(significant) < 4:
        return ""
    return " ".join(significant[:10])


def _publishable_card(item: dict[str, Any]) -> bool:
    score = _item_quality_score(item)
    if _is_non_article_item(item):
        return False
    topic = clean_text(item.get("primary_topic_display") or GENERIC_TOPIC)
    if topic == GENERIC_TOPIC and not _is_tech_relevant_item(item):
        return False
    if topic == GENERIC_TOPIC and not item.get("metadata_enriched"):
        return False
    if _has_card_evidence(item):
        return score >= -2
    # Mixed archives should not pad the publication with title-only shells.
    # The fallback path in _select_cards still preserves a minimal card for
    # fully thin synthetic or edge-case archives.
    return False



def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise NewsletterPostConfigError(f"invalid integer env var {name}: {raw}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise NewsletterPostConfigError(f"invalid float env var {name}: {raw}") from exc


def _default_card_news_audit_path() -> Path:
    override = os.environ.get("DISCORD_CARD_NEWS_AUDIT_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    wiki_root = os.environ.get("NEWSLETTER_WIKI_ROOT", "").strip()
    if wiki_root:
        return Path(wiki_root).expanduser() / "state" / "card-news-publication-audit.jsonl"
    return Path.home() / ".openclaw" / "state" / "discord-openclaw-bridge" / "card-news-publication-audit.jsonl"


def _card_news_quality_gate_config_from_env() -> CardNewsQualityGateConfig:
    return CardNewsQualityGateConfig(
        enabled=_env_flag("DISCORD_CARD_NEWS_QUALITY_GATE", "1"),
        audit_path=_default_card_news_audit_path(),
        history_days=_env_int("DISCORD_CARD_NEWS_HISTORY_DAYS", 14),
        min_publishable_cards=_env_int("DISCORD_CARD_NEWS_MIN_PUBLISHABLE_CARDS", 3),
        min_new_cards=_env_int("DISCORD_CARD_NEWS_MIN_NEW_CARDS", 3),
        max_previous_overlap_ratio=_env_float("DISCORD_CARD_NEWS_MAX_PREVIOUS_OVERLAP_RATIO", 0.5),
        min_evidence_cards=_env_int("DISCORD_CARD_NEWS_MIN_EVIDENCE_CARDS", 2),
        content_similarity_threshold=_env_float("DISCORD_CARD_NEWS_CONTENT_SIMILARITY_THRESHOLD", 0.72),
        agent_dedupe_enabled=_env_flag("DISCORD_CARD_NEWS_AGENT_DEDUPE", "0"),
        agent_dedupe_max_previous=_env_int("DISCORD_CARD_NEWS_AGENT_DEDUPE_MAX_PREVIOUS", 5),
        agent_dedupe_timeout_sec=_env_float("DISCORD_CARD_NEWS_AGENT_DEDUPE_TIMEOUT_SEC", 45.0),
    )


def _resolve_ops_bot_token() -> tuple[str, str]:
    guard_token = os.environ.get("DISCORD_GUARD_BOT_TOKEN", "").strip()
    if guard_token:
        return guard_token, "guard"
    bridge_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if bridge_token:
        return bridge_token, "bridge-fallback"
    raise NewsletterPostConfigError(
        "missing DISCORD_GUARD_BOT_TOKEN (preferred) and DISCORD_BOT_TOKEN (fallback) for ops reporting"
    )


def _card_news_skip_is_duplicate_related(evaluation: dict[str, Any]) -> bool:
    reason_codes = {str(reason) for reason in evaluation.get("reason_codes", [])}
    return bool(reason_codes & CARD_NEWS_DUPLICATE_SKIP_REASONS)


def _format_card_news_skip_ops_title(payload: dict[str, Any], evaluation: dict[str, Any]) -> str:
    run_date = clean_text(payload.get("date") or date.today().isoformat())
    counts = evaluation.get("counts") if isinstance(evaluation.get("counts"), dict) else {}
    new_count = counts.get("new", "?")
    overlap = counts.get("overlap_ratio", "?")
    return f"⚠️ Card News 보류 {run_date} — new={new_count} overlap={overlap}"[:90]


def _format_card_news_skip_ops_body(
    *,
    payload: dict[str, Any],
    source: Path,
    evaluation: dict[str, Any],
    audit_path: Path,
    token_source: str,
) -> str:
    run_date = clean_text(payload.get("date") or date.today().isoformat())
    counts = evaluation.get("counts") if isinstance(evaluation.get("counts"), dict) else {}
    thresholds = evaluation.get("thresholds") if isinstance(evaluation.get("thresholds"), dict) else {}
    reason_codes = [str(reason) for reason in evaluation.get("reason_codes", [])]
    duplicate_reasons = [reason for reason in reason_codes if reason in CARD_NEWS_DUPLICATE_SKIP_REASONS]
    lines = [
        f"**카드뉴스 발행 보류 — {run_date}**",
        "",
        "뉴스레타 아카이브는 생성/게시됐지만, 카드뉴스는 중복·신규성 게이트로 발행하지 않았습니다.",
        "",
        "**품질 게이트 판정**",
        f"- decision: `skip`",
        f"- reasons: `{', '.join(reason_codes) or 'unknown'}`",
        f"- duplicate/newness reasons: `{', '.join(duplicate_reasons) or 'none'}`",
        f"- selected: `{counts.get('selected', 0)}` publishable: `{counts.get('publishable', 0)}` evidence: `{counts.get('evidence', 0)}`",
        f"- new: `{counts.get('new', 0)}` repeated: `{counts.get('repeated', 0)}` overlap: `{counts.get('overlap_ratio', 0)}`",
        "",
        "**기준값**",
        f"- min_new_cards: `{thresholds.get('min_new_cards', '?')}`",
        f"- max_previous_overlap_ratio: `{thresholds.get('max_previous_overlap_ratio', '?')}`",
        f"- min_publishable_cards: `{thresholds.get('min_publishable_cards', '?')}`",
        f"- min_evidence_cards: `{thresholds.get('min_evidence_cards', '?')}`",
        "",
        "**근거 위치**",
        f"- source: `{_source_ref(source, payload)}`",
        f"- audit: `{audit_path}`",
        "",
        "**권장 조치**",
        "- 정상 중복 방지라면 조치 없음.",
        "- 운영상 강제 발행이 필요하면 일회성으로 품질 게이트 임계값을 완화하거나 `DISCORD_CARD_NEWS_QUALITY_GATE=0`을 명시해 재실행.",
        f"",
        f"_Reported by card-news quality gate via `{token_source}` ops identity._",
    ]
    body = "\n".join(lines)
    if len(body) > 1900:
        body = body[:1897].rstrip() + "..."
    return body


async def _post_card_news_skip_ops_report(
    client: httpx.AsyncClient,
    *,
    payload: dict[str, Any],
    source: Path,
    evaluation: dict[str, Any],
    audit_path: Path,
) -> str:
    if os.environ.get("DISCORD_CARD_NEWS_REPORT_SKIP_TO_OPS", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return ""
    if not _card_news_skip_is_duplicate_related(evaluation):
        return ""
    token, token_source = _resolve_ops_bot_token()
    channel_id = os.environ.get("DISCORD_OPS_REPORT_CHANNEL_ID", DEFAULT_OPS_REPORT_CHANNEL_ID).strip()
    if not channel_id:
        raise NewsletterPostConfigError("DISCORD_OPS_REPORT_CHANNEL_ID is empty")
    headers = {"Authorization": f"Bot {token}"}
    info = await client.get(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
    info.raise_for_status()
    if int(info.json().get("type", 0)) not in FORUM_CHANNEL_TYPES:
        raise NewsletterPostConfigError(f"DISCORD_OPS_REPORT_CHANNEL_ID={channel_id} is not a forum channel")
    response = await client.post(
        f"https://discord.com/api/v10/channels/{channel_id}/threads",
        headers=headers,
        json={
            "name": _format_card_news_skip_ops_title(payload, evaluation),
            "auto_archive_duration": 4320,
            "message": {
                "content": _format_card_news_skip_ops_body(
                    payload=payload,
                    source=source,
                    evaluation=evaluation,
                    audit_path=audit_path,
                    token_source=token_source,
                ),
                "allowed_mentions": {"parse": []},
                "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
            },
        },
    )
    response.raise_for_status()
    return str(response.json().get("id") or "")


def _card_identity_fingerprint(item: dict[str, Any]) -> str:
    url = sanitize_url(item.get("url"))
    if url:
        return "url:" + _hash_text(url.lower())
    story = _story_key(item) or _canonical_title_key(item)
    return "story:" + _hash_text(story.lower()) if story else ""


def _card_content_fingerprint(item: dict[str, Any]) -> str:
    parts = [
        _title(item),
        clean_text(item.get("primary_topic_display") or GENERIC_TOPIC),
        clean_text(item.get("hook") or item.get("why_now"), limit=360),
        clean_text(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=360),
        clean_text(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"), limit=360),
        clean_text(item.get("why_matters") or item.get("evidence"), limit=360),
        " ".join(_summary_lines(item)),
        _evidence_snippet(item, limit=360),
        clean_text(item.get("public_excerpt") or item.get("article_description"), limit=360),
    ]
    return _hash_text("\n".join(parts).lower())


_CONTENT_SIGNATURE_STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "your",
    "you",
    "are",
    "was",
    "were",
    "have",
    "has",
    "how",
    "why",
    "what",
    "newsletter",
    "weekly",
    "paper",
    "article",
    "github",
    "zoom",
    "luma",
    "공개",
    "요약",
    "기준",
    "원문",
    "확인",
    "필요",
    "카드",
    "뉴스",
}


def _card_similarity_text(item: dict[str, Any]) -> str:
    summary_lines = item.get("summary_lines")
    summary = " ".join(str(line) for line in summary_lines if line) if isinstance(summary_lines, list) else ""
    parts = [
        _raw_title(item),
        _title(item),
        clean_text(item.get("primary_topic_display") or GENERIC_TOPIC),
        _story_key(item),
        clean_text(item.get("hook") or item.get("why_now"), limit=360),
        clean_text(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=360),
        clean_text(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"), limit=360),
        clean_text(item.get("why_matters") or item.get("evidence"), limit=360),
        summary,
        _evidence_snippet(item, limit=360),
        clean_text(item.get("public_excerpt") or item.get("article_description"), limit=360),
    ]
    return " ".join(part for part in parts if part)


def _content_signature_tokens(item: dict[str, Any]) -> set[str]:
    text = _card_similarity_text(item).lower()
    words = [
        word
        for word in re.findall(r"[0-9a-z가-힣]+", text)
        if len(word) > 1 and word not in _CONTENT_SIGNATURE_STOP_WORDS
    ]
    tokens: set[str] = set()
    for word in words:
        tokens.add(word)
        # Long Korean/English compounds often arrive as one whitespace token.
        # Add bounded character shingles so near-identical titles/excerpts match
        # even when punctuation or a short prefix differs.
        if len(word) >= 6:
            width = 4 if re.search(r"[가-힣]", word) else 5
            tokens.update(word[index : index + width] for index in range(0, max(0, len(word) - width + 1)))
    return tokens


def _content_signature_hashes(item: dict[str, Any]) -> list[str]:
    return sorted(_hash_text(token) for token in _content_signature_tokens(item))


def _context_signature_hashes(context: dict[str, str]) -> set[str]:
    text = " ".join(str(value) for value in context.values() if value)
    return set(_content_signature_hashes({"article_title": text, "public_excerpt": text}))


def _agent_context(item: dict[str, Any]) -> dict[str, str]:
    summary_lines = item.get("summary_lines")
    summary = " ".join(str(line) for line in summary_lines if line) if isinstance(summary_lines, list) else ""
    return {
        "title": _title(item),
        "topic": clean_text(item.get("primary_topic_display") or GENERIC_TOPIC, limit=80),
        "story_key": clean_text(_story_key(item), limit=220),
        "claim": clean_text(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=360),
        "mechanism": clean_text(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"), limit=360),
        "evidence": clean_text(item.get("why_matters") or item.get("evidence"), limit=360),
        "summary": clean_text(summary, limit=360),
        "excerpt": clean_text(item.get("public_excerpt") or item.get("article_description"), limit=360),
    }


def _is_useful_agent_context(context: dict[str, str]) -> bool:
    return len(_context_signature_hashes(context)) >= 5


def _rank_agent_contexts_for_cards(
    cards: list[dict[str, Any]],
    previous_contexts: list[dict[str, str]],
    *,
    limit: int,
) -> list[dict[str, str]]:
    if not cards or not previous_contexts:
        return []
    current_signatures = [_context_signature_hashes(_agent_context(card)) for card in cards]
    ranked: list[tuple[float, int, dict[str, str]]] = []
    for index, context in enumerate(previous_contexts):
        previous_signature = _context_signature_hashes(context)
        score = max(
            (_content_signature_similarity(current, previous_signature) for current in current_signatures),
            default=0.0,
        )
        ranked.append((score, index, context))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    selected = [context for score, _index, context in ranked if score > 0][: max(1, limit)]
    if selected:
        return selected
    return previous_contexts[-max(1, limit) :]


def _content_signature_similarity(current: set[str], previous: set[str]) -> float:
    if len(current) < 5 or len(previous) < 5:
        return 0.0
    intersection = len(current & previous)
    if intersection < 4:
        return 0.0
    return intersection / len(current | previous)


def _is_content_similar_to_previous(
    item: dict[str, Any],
    previous_signatures: list[set[str]],
    *,
    threshold: float,
) -> bool:
    current = set(_content_signature_hashes(item))
    if len(current) < 5:
        return False
    return any(_content_signature_similarity(current, previous) >= threshold for previous in previous_signatures)


def _load_recent_published_card_history(
    audit_path: Path,
    *,
    history_days: int,
    now: datetime | None = None,
) -> tuple[set[str], list[set[str]]]:
    if not audit_path.exists():
        return set(), []
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max(0, history_days))
    identities: set[str] = set()
    signatures: list[set[str]] = []
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set(), []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("decision") != "publish":
            continue
        timestamp = clean_text(record.get("timestamp"))
        if timestamp:
            try:
                seen_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                seen_at = None
            if seen_at and seen_at < cutoff:
                continue
        for card in record.get("cards") or []:
            if isinstance(card, dict):
                identity = clean_text(card.get("identity_fingerprint"))
                if identity:
                    identities.add(identity)
                signature = card.get("content_signature")
                if isinstance(signature, list):
                    tokens = {clean_text(token) for token in signature if clean_text(token)}
                    if tokens:
                        signatures.append(tokens)
    return identities, signatures


def _load_recent_published_identities(audit_path: Path, *, history_days: int, now: datetime | None = None) -> set[str]:
    identities, _signatures = _load_recent_published_card_history(
        audit_path,
        history_days=history_days,
        now=now,
    )
    return identities


def _load_recent_published_agent_contexts(
    audit_path: Path,
    *,
    history_days: int,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    if not audit_path.exists():
        return []
    cutoff = (now or datetime.now(UTC)) - timedelta(days=max(0, history_days))
    contexts: list[dict[str, str]] = []
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("decision") != "publish":
            continue
        timestamp = clean_text(record.get("timestamp"))
        if timestamp:
            try:
                seen_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except ValueError:
                seen_at = None
            if seen_at and seen_at < cutoff:
                continue
        for card in record.get("cards") or []:
            if not isinstance(card, dict):
                continue
            context = card.get("agent_context")
            if not isinstance(context, dict):
                continue
            cleaned = {str(key): clean_text(value, limit=420) for key, value in context.items() if clean_text(value)}
            if cleaned and _is_useful_agent_context(cleaned):
                contexts.append(cleaned)
    return contexts


def _quality_thresholds(config: CardNewsQualityGateConfig) -> dict[str, int | float]:
    return {
        "history_days": config.history_days,
        "min_publishable_cards": config.min_publishable_cards,
        "min_new_cards": config.min_new_cards,
        "max_previous_overlap_ratio": config.max_previous_overlap_ratio,
        "min_evidence_cards": config.min_evidence_cards,
        "content_similarity_threshold": config.content_similarity_threshold,
        "agent_dedupe_enabled": int(config.agent_dedupe_enabled),
        "agent_dedupe_max_previous": config.agent_dedupe_max_previous,
    }


def _gateway_env_value(name: str) -> str:
    return os.environ.get(name, "").strip()


def _openclaw_gateway_token_from_env() -> str:
    token = _gateway_env_value("HERMES_GATEWAY_TOKEN")
    if token:
        return token
    token = _gateway_env_value("OPENCLAW_GATEWAY_TOKEN")
    if token:
        return token
    for name in ("HERMES_GATEWAY_TOKEN_FILE", "OPENCLAW_GATEWAY_TOKEN_FILE"):
        token_file = _gateway_env_value(name)
        if not token_file:
            continue
        path = Path(token_file).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return ""


def _agent_gateway_base_url_from_env() -> str:
    return (_gateway_env_value("HERMES_BASE_URL") or _gateway_env_value("OPENCLAW_BASE_URL") or "http://127.0.0.1:18789/v1").rstrip("/")


def _agent_gateway_model_from_env() -> str:
    return _gateway_env_value("HERMES_MODEL") or _gateway_env_value("OPENCLAW_MODEL") or "openclaw/clawbridge"


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


async def _agent_duplicate_indices(
    cards: list[dict[str, Any]],
    previous_contexts: list[dict[str, str]],
    config: CardNewsQualityGateConfig,
) -> set[int]:
    if not config.agent_dedupe_enabled or not cards or not previous_contexts:
        return set()
    token = _openclaw_gateway_token_from_env()
    if not token:
        return set()
    base_url = _agent_gateway_base_url_from_env()
    if not is_loopback_base_url(base_url):
        return set()
    model = _agent_gateway_model_from_env()
    current = [
        {"index": index, **_agent_context(card)}
        for index, card in enumerate(cards)
        if _is_useful_agent_context(_agent_context(card))
    ]
    previous = _rank_agent_contexts_for_cards(
        cards,
        previous_contexts,
        limit=config.agent_dedupe_max_previous,
    )
    if not current or not previous:
        return set()
    prompt = {
        "task": (
            "Decide whether each current card is the same article or same concrete story as any previous card. "
            "Judge by substantive context, not URL. Related topic alone is not duplicate. "
            "Return strict JSON only: {\"duplicates\":[{\"current_index\":0,\"reason\":\"same_article|same_story\"}]}."
        ),
        "current_cards": current,
        "previous_cards": previous,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "You are a conservative duplicate-publication reviewer. "
                "Mark duplicate only for the same article, same paper, same product announcement, "
                "or the same concrete story under another URL. Do not mark broad thematic overlap."
            ),
        },
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    policy = OpenClawGatewayPolicy.from_values(
        base_url=base_url,
        token=token,
        primary_model=model,
        timeout_sec=config.agent_dedupe_timeout_sec,
        user_agent="discord-openclaw-bridge/0.1-card-news-dedupe",
    )
    try:
        async with OpenClawGatewayClient(policy) as gateway:
            content = await gateway.chat_completion(model, messages, temperature=0, max_tokens=400)
    except Exception:
        return set()
    data = _extract_json_object(str(content))
    duplicates = data.get("duplicates")
    indices: set[int] = set()
    if isinstance(duplicates, list):
        for item in duplicates:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("current_index"))
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(cards):
                indices.add(index)
    return indices


def _evaluate_card_news_quality(
    cards: list[dict[str, Any]],
    previous_identities: set[str],
    config: CardNewsQualityGateConfig,
    previous_content_signatures: list[set[str]] | None = None,
    agent_repeated_indices: set[int] | None = None,
) -> dict[str, Any]:
    previous_content_signatures = previous_content_signatures or []
    agent_repeated_indices = agent_repeated_indices or set()
    identities = [_card_identity_fingerprint(item) for item in cards]
    selected_count = len(cards)
    publishable_count = sum(1 for item in cards if _publishable_card(item))
    evidence_count = sum(1 for item in cards if _has_card_evidence(item))
    identity_repeated_count = 0
    content_repeated_count = 0
    agent_repeated_count = 0
    repeated_count = 0
    for index, (item, identity) in enumerate(zip(cards, identities, strict=False)):
        repeated_by_identity = bool(identity and identity in previous_identities)
        repeated_by_content = _is_content_similar_to_previous(
            item,
            previous_content_signatures,
            threshold=config.content_similarity_threshold,
        )
        repeated_by_agent = index in agent_repeated_indices
        if repeated_by_identity:
            identity_repeated_count += 1
        if repeated_by_content:
            content_repeated_count += 1
        if repeated_by_agent:
            agent_repeated_count += 1
        if repeated_by_identity or repeated_by_content or repeated_by_agent:
            repeated_count += 1
    new_count = selected_count - repeated_count
    overlap_ratio = (repeated_count / selected_count) if selected_count else 0.0
    reason_codes: list[str] = []
    if publishable_count < config.min_publishable_cards:
        reason_codes.append("min_publishable_cards")
    if evidence_count < config.min_evidence_cards:
        reason_codes.append("min_evidence_cards")
    if new_count < config.min_new_cards:
        reason_codes.append("min_new_cards")
    if selected_count and overlap_ratio > config.max_previous_overlap_ratio:
        reason_codes.append("max_previous_overlap_ratio")
    if selected_count == 0:
        reason_codes.append("no_selected_cards")
    return {
        "decision": "skip" if reason_codes else "publish",
        "reason_codes": reason_codes,
        "thresholds": _quality_thresholds(config),
        "counts": {
            "selected": selected_count,
            "publishable": publishable_count,
            "evidence": evidence_count,
            "new": new_count,
            "repeated": repeated_count,
            "identity_repeated": identity_repeated_count,
            "content_repeated": content_repeated_count,
            "agent_repeated": agent_repeated_count,
            "overlap_ratio": round(overlap_ratio, 4),
        },
    }


def _source_ref(source: Path, payload: dict[str, Any]) -> str:
    run_date = clean_text(payload.get("date"))
    if run_date:
        return run_date
    return f"{source.name}:sha256:{_hash_text(str(source))}"


def _audit_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in cards:
        url = sanitize_url(item.get("url"))
        record: dict[str, Any] = {
            "title": _title(item),
            "topic": clean_text(item.get("primary_topic_display") or GENERIC_TOPIC, limit=60),
            "identity_fingerprint": _card_identity_fingerprint(item),
            "content_fingerprint": _card_content_fingerprint(item),
            "content_signature": _content_signature_hashes(item),
            "agent_context": _agent_context(item),
            "evidence_kind": _richness(item, raw_title=_raw_title(item)),
            "has_evidence": _has_card_evidence(item),
        }
        if url:
            record["url"] = url
        elif clean_text(item.get("url")):
            record["url_hash"] = _hash_text(clean_text(item.get("url")))
        records.append(record)
    return records


def _build_card_news_audit_record(
    *,
    decision: str,
    payload: dict[str, Any],
    source: Path,
    cards: list[dict[str, Any]],
    evaluation: dict[str, Any],
    publish_metadata: dict[str, Any] | None = None,
    failure_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat(),
        "run_date": clean_text(payload.get("date") or date.today().isoformat()),
        "decision": decision,
        "source_ref": _source_ref(source, payload),
        "thresholds": evaluation.get("thresholds", {}),
        "counts": evaluation.get("counts", {}),
        "reason_codes": evaluation.get("reason_codes", []),
        "cards": _audit_cards(cards),
    }
    if publish_metadata:
        record["publish"] = publish_metadata
    if failure_metadata:
        record["failure"] = failure_metadata
    return record


def _append_card_news_audit(audit_path: Path, record: dict[str, Any]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _semantic_family_key(item: dict[str, Any]) -> str:
    text = " ".join(
        clean_text(item.get(key), limit=240)
        for key in ("article_title", "title", "public_excerpt", "article_description", "summary", "description")
    )
    if _GRAPH_EMBEDDING_FAMILY_RE.search(text):
        return "graph_representation_learning"
    return ""


def _sort_by_quality(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (_item_quality_score(item), _url_quality(clean_text(item.get("url")))), reverse=True)


def _topic_groups(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        topic = clean_text(item.get("primary_topic_display") or GENERIC_TOPIC)
        groups.setdefault(topic, []).append(item)
    return dict(
        sorted(groups.items(), key=lambda pair: (TOPIC_PRIORITY.get(pair[0], 800), -len(pair[1]), pair[0]))
    )


def _select_cards(items: list[dict[str, Any]], *, max_cards: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    seen_stories: set[str] = set()
    seen_semantic: set[str] = set()
    for item in _sort_by_quality([item for item in items if _publishable_card(item)]):
        title_key = _canonical_title_key(item)
        story_key = _story_key(item)
        url = clean_text(item.get("url"))
        key = title_key or url
        semantic_key = _semantic_family_key(item)
        if (
            not url
            or not key
            or key in seen_titles
            or story_key in seen_stories
            or (semantic_key and semantic_key in seen_semantic)
        ):
            continue
        seen_titles.add(key)
        if story_key:
            seen_stories.add(story_key)
        if semantic_key:
            seen_semantic.add(semantic_key)
        selected.append(item)
        if len(selected) >= max_cards:
            break
    if not selected:
        # Preserve a minimal fallback for synthetic/unit-test payloads and true
        # edge cases where the archive has no publishable evidence at all.  In
        # real mixed archives, the stricter path above prevents title-only
        # shells from crowding out article-backed cards.
        for _topic, topic_items in _topic_groups(items).items():
            for item in _sort_by_quality(topic_items):
                title_key = _canonical_title_key(item)
                url = clean_text(item.get("url"))
                key = title_key or url
                semantic_key = _semantic_family_key(item)
                if not url or not key or key in seen_titles or (semantic_key and semantic_key in seen_semantic):
                    continue
                seen_titles.add(key)
                if semantic_key:
                    seen_semantic.add(semantic_key)
                selected.append(item)
                break
            if len(selected) >= max_cards:
                break
    return selected


def _first_sentence(text: str) -> str:
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?。])\s+", text.strip(), maxsplit=1)
    return parts[0].strip() if parts else text.strip()


def _distinct_topics_in_order(cards: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for item in cards:
        topic = clean_text(item.get("primary_topic_display") or GENERIC_TOPIC)
        if topic and topic not in seen:
            seen.append(topic)
    return sorted(seen, key=lambda t: TOPIC_PRIORITY.get(t, 800))


def _evidence_snippet(item: dict[str, Any], *, limit: int = 220) -> str:
    raw_title = _raw_title(item)
    candidates: list[str] = [
        clean_text(item.get("hook") or item.get("why_now")),
        clean_text(item.get("core_change") or item.get("claim") or item.get("thesis")),
        *_summary_lines(item),
        clean_text(item.get("context") or item.get("mechanism") or item.get("claim_mechanism")),
        clean_text(item.get("why_matters") or item.get("evidence")),
        _substantive_excerpt(item, raw_title=raw_title),
    ]
    for candidate in candidates:
        text = _first_sentence(_normalize_register(clean_text(candidate, limit=limit)))
        if text:
            return text
    return ""


def _has_hangul(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


def _korean_takeaway(item: dict[str, Any], *, limit: int = 360) -> str:
    """Create Korean technical interpretation from public title/excerpt facts."""
    title = _raw_title(item)
    snippet = _evidence_snippet(item, limit=520)
    haystack = f"{title} {snippet}".lower()

    if "topology-aware representation alignment" in haystack or "vision-language" in haystack:
        text = (
            "이 논문의 출발점은 CLIP류 비전-언어 모델이 일반 벤치마크에서는 강하지만 위성·패션·의료처럼 "
            "분포가 다른 도메인에서는 쉽게 흔들린다는 점입니다. ToMA는 이미지-텍스트 쌍을 하나씩 맞추는 데서 "
            "멈추지 않고, 임베딩 공간의 연결·순환 구조를 지속적 호몰로지로 잡아 두 모달리티의 전역 구조를 함께 맞추려 합니다."
        )
        return clean_text(text, limit=limit)

    if "ai-native products grow differently" in haystack or ("old saas playbook" in haystack and "trust" in haystack):
        text = (
            "AI-native 제품의 차이는 AI 기능을 붙였다는 데 있지 않고, 사용 맥락·결과 달성·신뢰 형성을 제품 루프 안에 "
            "넣는 데 있습니다. 기존 SaaS가 정해진 워크플로우에 모델을 덧붙였다면, AI-native 제품은 데이터 피드백, "
            "비용 변동성, 사용자 신뢰를 제품 성장의 핵심 변수로 다뤄야 합니다."
        )
        return clean_text(text, limit=limit)

    if "knowledge graph health" in haystack or "structure before scale" in haystack:
        text = (
            "이 글의 핵심은 지식그래프를 크게 만드는 것보다 먼저 노드·엣지의 품질과 연결 구조를 점검해야 한다는 주장입니다. "
            "GraphRAG에서는 구조가 불안정하면 검색이 빨라질수록 잘못된 근거도 더 빠르게 퍼지므로, 규모보다 상태 진단이 먼저입니다."
        )
        return clean_text(text, limit=limit)

    if "rag, llm wiki, or gbrain" in haystack or "agent remembers" in haystack:
        text = (
            "에이전트의 기억 구조는 단순 저장소 선택이 아니라 장기 실행 품질을 좌우하는 설계 문제입니다. "
            "RAG, 위키형 메모리, 그래프형 기억은 각각 검색 속도, 근거 추적성, 갱신 비용이 다르기 때문에 제품 단계에서는 "
            "정답률보다 기억의 출처와 실패 복구 방식을 함께 봐야 합니다."
        )
        return clean_text(text, limit=limit)

    if "hybrid search" in haystack and "bm25" in haystack:
        text = (
            "하이브리드 RAG의 요지는 벡터 검색 하나로 근거 검색을 끝내지 않는 데 있습니다. BM25, 임베딩 검색, 재랭킹을 "
            "함께 쓰면 키워드 일치와 의미 유사도를 보완할 수 있지만, 운영 환경에서는 지연시간과 평가 로그 설계가 같이 따라와야 합니다."
        )
        return clean_text(text, limit=limit)

    if _has_hangul(snippet):
        return clean_text(_normalize_register(snippet), limit=limit)

    topic = clean_text(item.get("primary_topic_display") or GENERIC_TOPIC)
    if topic == "논문/리서치":
        text = (
            f"{_title(item)}는 공개 초록 기준으로 새 방법이나 평가 조건을 제시한 연구 후보입니다. "
            "다만 성능 주장보다 데이터셋, 비교군, 재현 코드가 실제 도입 판단의 핵심입니다."
        )
    elif topic == "LLM/에이전트":
        text = (
            f"{_title(item)}는 에이전트 제품에서 모델 능력보다 운영 루프와 신뢰 설계가 중요하다는 신호입니다. "
            "도입자는 비용, 실패 복구, 사용자 피드백이 제품 구조 안에 들어가는지 확인해야 합니다."
        )
    else:
        text = (
            f"{_title(item)}는 공개 요약 기준으로 기술 선택의 방향을 보여주는 후보입니다. "
            "본문 판단은 원문이 제시한 조건과 실제 운영 제약을 대조해야 합니다."
        )
    return clean_text(text, limit=limit)


def _evidence_records(cards: list[dict[str, Any]], *, limit: int = 4) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for item in cards:
        snippet = _evidence_snippet(item, limit=240)
        if not snippet:
            continue
        records.append(
            {
                "title": _title(item),
                "topic": clean_text(item.get("primary_topic_display") or GENERIC_TOPIC, limit=60),
                "snippet": snippet,
                "takeaway": _korean_takeaway(item, limit=360),
            }
        )
        if len(records) >= limit:
            break
    return records


def _strip_sentence_end(text: str) -> str:
    return clean_text(text).rstrip(".!?。")


def _decision_axis(topics: list[str], records: list[dict[str, str]]) -> str:
    topic_text = " ".join(topics)
    title_text = " ".join([record["title"] + " " + record.get("takeaway", "") for record in records]).lower()
    if ("vision-language" in title_text or "비전-언어" in title_text) and (
        "ai-native" in title_text or "ai native" in title_text
    ):
        return "모델 성능을 현장 데이터, 제품 피드백, 사용자 신뢰 구조에 맞게 다시 설계할지"
    if "검색/RAG/지식그래프" in topic_text and "LLM/에이전트" in topic_text:
        return "에이전트가 무엇을 기억하고, 어떤 검색 근거를 신뢰하며, 그 결과를 어떻게 평가할지"
    if "검색/RAG/지식그래프" in topic_text:
        return "RAG를 단순 벡터 검색이 아니라 그래프 구조·키워드 검색·재랭킹을 결합한 근거 파이프라인으로 설계할지"
    if "LLM/에이전트" in topic_text:
        return "장기 실행 에이전트의 메모리, 도구 호출, 비용 통제를 제품 구조 안에 어떻게 넣을지"
    if "멀티모달/비전" in topic_text or "vision-language" in title_text:
        return "범용 비전-언어 모델을 도메인 데이터와 평가 조건에 맞게 정렬할지"
    if "오픈소스/코드" in topic_text:
        return "논문 구현체를 재현 가능한 코드와 운영 가능한 실험 자산으로 전환할지"
    return "새 기술 신호를 제품 의사결정, 평가 조건, 운영 책임으로 번역할지"


def _evidence_count(cards: list[dict[str, Any]]) -> int:
    return sum(1 for item in cards if _evidence_snippet(item))


def _thin_titles(cards: list[dict[str, Any]], *, limit: int = 2) -> list[str]:
    titles: list[str] = []
    for item in cards:
        if _evidence_snippet(item):
            continue
        title = _title(item)
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def _theme_sentence(cards: list[dict[str, Any]]) -> str:
    distinct = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    records = _evidence_records(cards, limit=2)
    axis = _decision_axis(distinct, records)
    if len(records) >= 2:
        return f"{records[0]['title']}와 {records[1]['title']}를 함께 읽으면 쟁점은 {axis}입니다."
    if records:
        return f"{records[0]['title']}가 던지는 질문은 {axis}입니다."
    for item in cards:
        why_now = clean_text(item.get("why_now"), limit=240)
        if why_now:
            return _normalize_register(_first_sentence(why_now))
    return f"오늘 수집분은 {axis}를 확인해야 하는 후보로 남았습니다."


def _hero_image_description(topics: list[str]) -> str:
    topic_text = " · ".join(topics[:3]) if topics else "기술 브리핑"
    return (
        f"{topic_text} 변화가 연구 현장, 제품 조직, 운영 대시보드로 이어지는 장면을 "
        "추상적으로 표현한 대표 이미지. 제목과 핵심 키워드는 별도 오버레이로 넣고, "
        "로고·실존 인물 초상·원문 스크린샷은 제외."
    )


def _three_line_summary(cards: list[dict[str, Any]], theme: str) -> list[str]:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    records = _evidence_records(cards, limit=2)
    axis = _decision_axis(topics, records)
    evidence_count = _evidence_count(cards)
    if records:
        first = records[0]["takeaway"]
    else:
        first = theme
    if len(records) >= 2:
        second = f"{records[1]['title']}까지 함께 보면, 관건은 {axis}입니다."
    else:
        second = f"관건은 {axis}입니다."
    third = f"공개 요약·초록이 있는 {evidence_count}/{len(cards)}건만 본문 근거로 쓰고, 메일 본문·토큰·비밀값은 제외했습니다."
    return [clean_text(first, limit=170), clean_text(second, limit=170), clean_text(third, limit=170)]


def _article_thesis(cards: list[dict[str, Any]], theme: str) -> str:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    records = _evidence_records(cards, limit=2)
    axis = _decision_axis(topics, records)
    if records:
        basis = "; ".join(
            f"{record['title']}는 {_strip_sentence_end(record['takeaway'])}라고 해석됩니다"
            for record in records
        )
        return clean_text(f"핵심은 {axis}입니다. 근거는 {basis}.", limit=280)
    for item in cards:
        claim = _normalize_register(
            clean_text(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=180)
        )
        if claim:
            return claim
    return theme


def _argument_structure(cards: list[dict[str, Any]], theme: str) -> list[str]:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    records = _evidence_records(cards, limit=3)
    axis = _decision_axis(topics, records)
    evidence_count = _evidence_count(cards)
    if records:
        observation = "관찰: " + "; ".join(
            f"{record['title']}는 {_strip_sentence_end(record['takeaway'])}라고 읽힙니다"
            for record in records[:2]
        )
    else:
        observation = f"관찰: {theme}"
    thin = _thin_titles(cards)
    tension_tail = (
        f" 다만 {', '.join(thin)}는 공개 요약이 얇아 제목 신호로만 다룹니다."
        if thin
        else ""
    )
    return [
        clean_text(observation, limit=240),
        f"메커니즘: {axis} 때문에 도입자는 모델 성능뿐 아니라 데이터 구조, 메모리 저장 방식, 평가 로그를 함께 설계해야 합니다.",
        f"긴장: 공개 근거가 있는 항목은 {evidence_count}/{len(cards)}건입니다.{tension_tail} 기술 선택을 서두르면 성능 수치보다 운영 비용과 검증 책임이 먼저 누락됩니다.",
        "반론: 공개 요약·초록은 1차 설명이므로 성능 우위나 제품 효과는 원문 실험 조건 확인 전까지 보류합니다.",
        f"판단: 지금 말할 수 있는 결론은 {axis}가 반복 쟁점이라는 점입니다.",
    ]


def _industry_interpretation(cards: list[dict[str, Any]]) -> str:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    records = _evidence_records(cards, limit=1)
    axis = _decision_axis(topics, records)
    lead = records[0]["title"] if records else "이번 브리핑"
    if not topics:
        return clean_text(f"{lead}는 기술 후보를 바로 채택하기보다 공개 근거와 재현 조건을 먼저 확인해야 함을 보여줍니다.", limit=240)
    return clean_text(
        f"{lead}의 쟁점은 연구 성과 자체보다 {axis}를 조직이 어떻게 책임질지에 가깝습니다. "
        "현장에서는 데이터 소유권, 인덱스 갱신 주기, 실패 로그, 비용 배분을 정하지 않으면 좋은 모델도 운영 리스크로 바뀝니다.",
        limit=280,
    )


def _future_questions(cards: list[dict[str, Any]]) -> list[str]:
    questions: list[str] = []
    for record in _evidence_records(cards, limit=4):
        title = clean_text(record["title"], limit=56)
        topic = record["topic"]
        if topic == "검색/RAG/지식그래프":
            qtext = f"{title}의 검색 구조는 우리 문서 권한, 최신성, 지연시간 조건에서도 같은 이득을 내는가?"
        elif topic == "LLM/에이전트":
            qtext = f"{title}의 에이전트 설계는 장기 실행 비용과 실패 복구 책임을 어디에 배치하는가?"
        elif topic == "멀티모달/비전":
            qtext = f"{title}의 평가 결과는 특정 도메인 이미지와 텍스트 분포가 바뀌어도 유지되는가?"
        elif topic == "논문/리서치":
            qtext = f"{title}의 데이터셋·비교군·재현 코드는 실제 도입 판단에 충분한가?"
        else:
            qtext = f"{title}가 제시한 변화는 제품 지표, 운영 비용, 사용자 신뢰 중 무엇을 먼저 바꾸는가?"
        if qtext not in questions:
            questions.append(qtext)
        if len(questions) >= 2:
            break
    while len(questions) < 2:
        fallback = (
            "공개 요약이 없는 항목은 원문 실험 조건을 확인했을 때 같은 주장으로 유지되는가?"
            if not questions
            else "이 기술을 제품에 넣을 때 정확도, 비용, 보안 책임 중 어느 항목이 병목이 되는가?"
        )
        if fallback not in questions:
            questions.append(fallback)
    return questions[:2]


def _why_now_paragraph(cards: list[dict[str, Any]], theme: str) -> str:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    records = _evidence_records(cards, limit=2)
    axis = _decision_axis(topics, records)
    if len(records) >= 2:
        return clean_text(
            f"{records[0]['title']}는 {records[0]['takeaway']} "
            f"여기에 {records[1]['title']}가 제기한 쟁점까지 붙이면, 이번 브리핑의 질문은 새 도구 소개가 아니라 {axis}입니다.",
            limit=430,
        )
    if records:
        return clean_text(
            f"{records[0]['title']}는 {records[0]['takeaway']} "
            f"그래서 이번 이슈는 단일 링크 소개가 아니라 {axis}라는 운영 질문으로 읽어야 합니다.",
            limit=360,
        )
    return theme


def _source_basis_sentence(cards: list[dict[str, Any]], *, item_count: int) -> str:
    records = _evidence_records(cards, limit=3)
    evidence_count = _evidence_count(cards)
    if records:
        titles = ", ".join(record["title"] for record in records)
        return clean_text(
            f"근거: 선별 {len(cards)}건 / 수집 {item_count}건 중 공개 요약·초록이 확인된 "
            f"{evidence_count}건을 본문 근거로 사용했습니다. 대표 근거는 {titles}입니다.",
            limit=260,
        )
    return f"근거: 선별 {len(cards)}건 / 수집 {item_count}건 중 공개 요약이 부족해 제목과 원문 링크만 남겼습니다."


def _render_article_header(cards: list[dict[str, Any]], *, run_date: str, item_count: int) -> str:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    theme = _theme_sentence(cards)
    summary = _three_line_summary(cards, theme)
    argument = _argument_structure(cards, theme)
    questions = _future_questions(cards)
    lines = [
        f"**{CARD_NEWS_TITLE} — 기술 블로그 브리핑 — {run_date}**",
        "",
        f"대표 이미지(설명): {_hero_image_description(topics)}",
        "",
        "> 3줄 요약",
        f"> 1. {summary[0]}",
        f"> 2. {summary[1]}",
        f"> 3. {summary[2]}",
        "",
        "## 왜 지금 이 이슈인가",
        _why_now_paragraph(cards, theme),
        "",
        "## 핵심 주장",
        f"- 주장: {_article_thesis(cards, theme)}",
        f"- {_source_basis_sentence(cards, item_count=item_count)}",
        "",
        "## 논증 구조",
    ]
    lines.extend(f"{idx}. {clean_text(line, limit=210)}" for idx, line in enumerate(argument, start=1))
    lines += [
        "",
        "## 산업사회학적·현장기반 해석",
        _industry_interpretation(cards),
        "",
        "## 앞으로 볼 질문",
        f"- {questions[0]}",
        f"- {questions[1]}",
        "",
        "## 카드뉴스·Discord 재사용안",
        "아래 카드는 핵심 쟁점, 공개 근거, 확인 한계를 분리해 Discord에서 바로 토론할 수 있게 나눈 블록입니다.",
        "",
        "## 출처",
        "각 카드 하단의 공개 URL만 사용합니다.",
    ]
    return "\n".join(lines)


def _is_interrogative(text: str) -> bool:
    if not text:
        return False
    if text.endswith("?"):
        return True
    stripped = text.rstrip(".!?。")
    return any(stripped.endswith(suffix) for suffix in _INTERROGATIVE_SUFFIXES)


def _question_or_transform(seed: str) -> str | None:
    if not seed:
        return None
    if _is_interrogative(seed):
        if seed.endswith("?"):
            return seed
        return seed.rstrip(".!?。") + "?"
    for pattern, replacement in _QUESTION_TRANSFORMS:
        if pattern.match(seed):
            return pattern.sub(replacement, seed)
    return None


def _has_action_signal(text: str) -> bool:
    return any(term in text for term in _ACTION_SIGNAL_TERMS)


def _maybe_implication(seed: str, *, has_claim: bool, has_evidence: bool) -> str | None:
    if not seed or not _has_action_signal(seed):
        return None
    if has_evidence and not has_claim:
        return f"다만 {seed}"
    return f"따라서 {seed}"


def _format_claim_mechanism(claim: str, mechanism: str) -> str:
    """Combine claim and mechanism in a single paragraph (frame-1 style).

    Always bridges with the bare adverb ``구체적으로`` (no 는). Preserving the
    topic marker on the connector creates a ``구체적으로는 X은/는`` collision
    whenever the mechanism's subject also carries 은/는 — which can land on any
    of the first few tokens, not just the first word. Dropping 는 here reads
    equally well in Korean prose and eliminates the entire collision class.
    """
    if claim and mechanism:
        return f"{claim} 구체적으로 {mechanism}"
    if claim:
        return claim
    if mechanism:
        return f"구체적으로 {mechanism}"
    return ""


def _build_frame1_paragraphs(
    *,
    why_now: str,
    summary: list[str],
    claim: str,
    mechanism: str,
    evidence: str,
    source: str,
) -> list[str]:
    paragraphs: list[str] = []

    if why_now:
        paragraphs.append(why_now)
    elif summary:
        paragraphs.append(_normalize_register(summary[0]))

    combined = _format_claim_mechanism(claim, mechanism)
    if combined:
        paragraphs.append(combined)

    if evidence:
        paragraphs.append(f"> {source}: {evidence}")

    if len(summary) >= 2:
        seed = _normalize_register(summary[1])
        implication = _maybe_implication(seed, has_claim=bool(claim), has_evidence=bool(evidence))
        if implication:
            paragraphs.append(implication)

    return paragraphs


def _build_frame2_paragraphs(
    *,
    why_now: str,
    summary: list[str],
    claim: str,
    mechanism: str,
    evidence: str,
) -> list[str]:
    paragraphs: list[str] = []

    if claim:
        paragraphs.append(claim)
    elif why_now:
        paragraphs.append(why_now)
    elif summary:
        paragraphs.append(_normalize_register(summary[0]))

    if evidence:
        paragraphs.append(f"다만 {evidence}")

    if mechanism:
        paragraphs.append(f"구체적으로 {mechanism}")

    seeds: list[str] = []
    if summary:
        seeds.append(_normalize_register(summary[-1]))
    if why_now and why_now != claim:
        seeds.append(why_now)
    if summary and len(summary) >= 1:
        seeds.append(_normalize_register(summary[0]))
    for candidate in seeds:
        qtext = _question_or_transform(candidate)
        if qtext and not any(_has_substring_overlap(p, qtext) for p in paragraphs):
            paragraphs.append(qtext)
            break

    return paragraphs


def _render_rich_card(
    item: dict[str, Any],
    *,
    title: str,
    topic: str,
    source: str,
    url: str,
    bucket: str,
    reasons: list[str] | None = None,
    frame: int = 1,
) -> str:
    summary = _summary_lines(item)
    why_now = _normalize_register(clean_text(item.get("hook") or item.get("why_now"), limit=240))
    claim = _normalize_register(clean_text(item.get("core_change") or item.get("claim") or item.get("thesis"), limit=220))
    mechanism = _normalize_register(clean_text(item.get("context") or item.get("mechanism") or item.get("claim_mechanism"), limit=220))
    evidence = _normalize_register(clean_text(item.get("why_matters") or item.get("evidence"), limit=220))
    cta = _normalize_register(clean_text(item.get("cta") or item.get("save_point"), limit=180))

    if frame == 2:
        paragraphs = _build_frame2_paragraphs(
            why_now=why_now,
            summary=summary,
            claim=claim,
            mechanism=mechanism,
            evidence=evidence,
        )
        emit_next_question_label = False
    else:
        paragraphs = _build_frame1_paragraphs(
            why_now=why_now,
            summary=summary,
            claim=claim,
            mechanism=mechanism,
            evidence=evidence,
            source=source,
        )
        emit_next_question_label = True

    if cta and not any(_has_substring_overlap(p, cta) for p in paragraphs):
        paragraphs.append(cta)

    paragraphs = _dedup_paragraphs(paragraphs)

    next_question_text: str | None = None
    if emit_next_question_label and summary:
        seed = _normalize_register(summary[-1])
        qtext = _question_or_transform(seed)
        if qtext and not any(_has_substring_overlap(p, qtext) for p in paragraphs):
            next_question_text = qtext

    body_parts: list[str] = [
        CARD_SEPARATOR,
        f"**{title}**",
        "",
    ]
    for para in paragraphs:
        body_parts.append(para)
        body_parts.append("")
    if next_question_text:
        body_parts.append("**다음 질문**")
        body_parts.append(next_question_text)
        body_parts.append("")
    body_parts.append(_format_footer(source, url, topic, bucket, reasons=reasons))
    return "\n".join(body_parts)


def _render_lean_card(
    item: dict[str, Any],
    *,
    title: str,
    topic: str,
    source: str,
    url: str,
    bucket: str,
    reasons: list[str] | None = None,
) -> str:
    excerpt = _normalize_register(_substantive_excerpt(item, raw_title=title)[:320])
    takeaway = _korean_takeaway(item, limit=360) if excerpt else ""
    disclaimer = LEAN_DISCLAIMER_WITH_EXCERPT if excerpt else LEAN_DISCLAIMER_WITHOUT_EXCERPT
    body_parts = [
        CARD_SEPARATOR,
        f"**{title}**",
        "",
    ]
    if takeaway:
        body_parts.extend([takeaway, ""])
    if excerpt:
        if not _has_substring_overlap(takeaway, excerpt, min_len=24):
            body_parts.extend([f"원문 단서: {clean_text(excerpt, limit=180)}", ""])
    body_parts.extend(
        [
            disclaimer,
            "",
            _format_footer(source, url, topic, bucket, reasons=reasons),
        ]
    )
    return "\n".join(body_parts)


def _render_skeletal_card(
    *,
    title: str,
    topic: str,
    url: str,
    bucket: str,
) -> str:
    follow_up_topic = topic if topic and topic != GENERIC_TOPIC else "기술"
    title_signal = title.rstrip(".")
    body_parts = [
        CARD_SEPARATOR,
        f"**{title}**",
        (
            f"수집 제목 기준으로는 {follow_up_topic} 영역에서 `{title_signal}` 문제를 다룹니다. "
            "공개 요약을 가져오지 못해 세부 근거는 원문에서 확인해야 합니다."
        ),
        f"<{sanitize_url(url)}> · `{bucket}`",
    ]
    return "\n".join(body_parts)




def _first_nonempty(values: list[str], *, fallback: str = "") -> str:
    for value in values:
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return fallback


def _publication_summary_lines(cards: list[dict[str, Any]]) -> list[str]:
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    summary: list[str] = []
    if topics:
        summary.append(f"오늘 브리핑은 {', '.join(topics[:3])} 흐름을 하나의 기술 변화로 묶어 읽습니다.")
    else:
        summary.append("오늘 브리핑은 공개 근거가 확인된 기술 읽기 후보를 선별해 점검합니다.")

    claim = ""
    for item in cards:
        claim = _first_nonempty([
            str(item.get("core_change") or ""),
            str(item.get("claim") or ""),
            str(item.get("thesis") or ""),
            *(_summary_lines(item)[:1]),
            _substantive_excerpt(item, raw_title=_raw_title(item)),
        ])
        if claim:
            break
    summary.append(_normalize_register(claim) if claim else "개별 링크보다 문제의식과 근거 수준을 먼저 분리해 읽어야 합니다.")

    question = ""
    for item in cards:
        lines = _summary_lines(item)
        if lines:
            question = _question_or_transform(_normalize_register(lines[-1])) or ""
        if question:
            break
    summary.append(question or "다음 실행에서는 원문 근거와 운영 적용 조건을 함께 확인해야 합니다.")
    return [clean_text(line, limit=180) for line in summary[:3]]


def _publication_header(cards: list[dict[str, Any]], *, run_date: str, total_count: int) -> str:
    summary = _publication_summary_lines(cards)
    theme = _theme_sentence(cards)
    topics = [t for t in _distinct_topics_in_order(cards) if t and t != GENERIC_TOPIC]
    topic_text = ", ".join(topics[:3]) if topics else "기술 브리핑 후보"
    first_title = _title(cards[0]) if cards else "수집 결과 없음"
    core_claim = summary[1]
    future_question = summary[2].rstrip(".")
    if not future_question.endswith("?"):
        future_question = f"{future_question}?"
    lines = [
        f"**집현전-Claw 기술 블로그 브리핑 — {run_date}**",
        f"대표 이미지 설명: {topic_text}를 데이터 흐름과 현장 의사결정 보드로 은유한 추상 일러스트",
        "",
        "**3줄 요약**",
        f"1. {summary[0]}",
        f"2. {summary[1]}",
        f"3. {summary[2]}",
        "",
        "**왜 지금인가**",
        theme,
        "",
        "**핵심 주장**",
        core_claim,
        "",
        "**논증 구조**",
        f"관찰: {topic_text} 신호가 공개 원문에서 반복됩니다. 메커니즘: 수집된 근거는 방법·평가·적용 조건의 차이를 드러냅니다. 긴장: 도입자는 정확도, 비용, 지연시간, 검증 가능성을 함께 부담합니다. 판단: 출처가 확인된 항목부터 좁게 읽어야 합니다.",
        "",
        "**산업/현장 해석**",
        f"{first_title} 같은 항목은 개인 영웅담보다 조직의 도구 선택, 평가 체계, 운영 비용 배분 문제로 읽어야 합니다.",
        "",
        "**앞으로 볼 질문**",
        future_question,
        "",
        "**카드뉴스·Discord 재사용안**",
        f"아래 {len(cards)}개 카드는 이 글의 주장과 근거를 Discord에서 재사용할 수 있게 나눈 섹션입니다.",
        "",
        f"선별 {len(cards)}건 / 수집 {total_count}건",
    ]
    return "\n".join(lines)

def _render_card_news_messages_from_cards(
    payload: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    item_count: int,
) -> list[str]:
    run_date = clean_text(payload.get("date") or date.today().isoformat())

    header = _render_article_header(cards, run_date=run_date, item_count=item_count)
    messages = [header]
    topic_index: dict[str, int] = {}

    for item in cards:
        raw_title = _raw_title(item)
        title = _title(item)
        topic = clean_text(item.get("primary_topic_display") or GENERIC_TOPIC, limit=60)
        source = _source_name(item)
        url = clean_text(item.get("url"))
        bucket = _confidence_bucket(item.get("topic_confidence"))
        raw_reasons = item.get("topic_reasons") or []
        reasons: list[str] = []
        if isinstance(raw_reasons, list):
            for raw in raw_reasons:
                cleaned = clean_text(raw, limit=40)
                if cleaned and cleaned not in reasons:
                    reasons.append(cleaned)
                if len(reasons) >= 2:
                    break
        kind = _richness(item, raw_title=raw_title)
        if kind == "rich":
            idx = topic_index.get(topic, 0)
            topic_index[topic] = idx + 1
            frame = 2 if idx % 2 == 1 else 1
            messages.append(
                _render_rich_card(
                    item,
                    title=title,
                    topic=topic,
                    source=source,
                    url=url,
                    bucket=bucket,
                    reasons=reasons,
                    frame=frame,
                )
            )
        elif kind == "lean":
            messages.append(
                _render_lean_card(
                    item,
                    title=title,
                    topic=topic,
                    source=source,
                    url=url,
                    bucket=bucket,
                    reasons=reasons,
                )
            )
        else:
            messages.append(
                _render_skeletal_card(title=title, topic=topic, url=url, bucket=bucket)
            )
    return messages


def render_card_news_messages(payload: dict[str, Any], *, max_cards: int = 8) -> list[str]:
    items = [item for item in payload.get("items", []) if isinstance(item, dict)]
    cards = _select_cards(items, max_cards=max_cards)
    return _render_card_news_messages_from_cards(payload, cards, item_count=len(items))


def _split_discord_content(content: str, *, limit: int = 1900) -> list[str]:
    if len(content) <= limit:
        return [content]
    chunks: list[str] = []
    current = ""
    parts = content.split("\n\n")
    for part in parts:
        candidate = part if not current else f"{current}\n\n{part}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(part) > limit:
            chunks.append(part[:limit].rstrip())
            part = part[limit:].lstrip()
        current = part
    if current:
        chunks.append(current)
    return chunks or [content[:limit]]


def _is_card_news_bot_message(message: dict[str, object]) -> bool:
    content = str(message.get("content") or "")
    author = message.get("author")
    author_is_bot = isinstance(author, dict) and bool(author.get("bot"))
    return author_is_bot and CARD_NEWS_TITLE in content


async def _purge_previous_card_news_messages(
    client: httpx.AsyncClient,
    messages_url: str,
    *,
    headers: dict[str, str],
    limit: int = 50,
) -> int:
    response = await client.get(f"{messages_url}?limit={limit}", headers=headers)
    response.raise_for_status()
    deleted = 0
    for message in response.json():
        if not isinstance(message, dict) or not _is_card_news_bot_message(message):
            continue
        message_id = str(message.get("id") or "")
        if not message_id:
            continue
        await _delete_message_with_rate_limit(client, f"{messages_url}/{message_id}", headers=headers)
        deleted += 1
    return deleted


async def _delete_channel_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
) -> None:
    while True:
        response = await client.delete(url, headers=headers)
        if response.status_code != 429:
            response.raise_for_status()
            return
        retry_after = float(response.json().get("retry_after", 1.0))
        await asyncio.sleep(retry_after)


async def _purge_previous_card_news_threads(
    client: httpx.AsyncClient,
    active_threads_url: str,
    *,
    headers: dict[str, str],
) -> int:
    response = await client.get(active_threads_url, headers=headers)
    response.raise_for_status()
    purged = 0
    threads = response.json().get("threads", [])
    if not isinstance(threads, list):
        return 0
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        name = str(thread.get("name") or "")
        thread_id = str(thread.get("id") or "")
        if not thread_id or not any(marker in name for marker in CARD_NEWS_THREAD_NAME_MARKERS):
            continue
        try:
            await _delete_channel_with_rate_limit(
                client,
                f"https://discord.com/api/v10/channels/{thread_id}",
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                patch = await client.patch(
                    f"https://discord.com/api/v10/channels/{thread_id}",
                    headers=headers,
                    json={"archived": True, "locked": False},
                )
                if patch.status_code == 404:
                    continue
                patch.raise_for_status()
                purged += 1
                continue
            if exc.response.status_code != 404:
                raise
            continue
        else:
            purged += 1
    return purged


async def _create_forum_card_news_thread(
    client: httpx.AsyncClient,
    forum_url: str,
    *,
    headers: dict[str, str],
    name: str,
    content: str,
    hero_image_path: Path | None = None,
    max_retries: int = 4,
) -> str:
    payload: dict[str, Any] = {
        "name": clean_text(name, limit=90),
        "auto_archive_duration": 1440,
        "message": {
            "content": content,
            "allowed_mentions": {"parse": []},
            "flags": DISCORD_SUPPRESS_EMBEDS_FLAG,
        },
    }
    if hero_image_path and hero_image_path.exists():
        message_payload = payload["message"]
        if isinstance(message_payload, dict):
            message_payload["attachments"] = [{"id": 0, "filename": hero_image_path.name}]
    thread_url = f"{forum_url}/threads"
    for attempt in range(max_retries + 1):
        try:
            if hero_image_path and hero_image_path.exists():
                response = await client.post(
                    thread_url,
                    headers=headers,
                    data={"payload_json": json.dumps(payload, ensure_ascii=False)},
                    files={"files[0]": (hero_image_path.name, hero_image_path.read_bytes(), "image/png")},
                )
            else:
                response = await client.post(thread_url, headers=headers, json=payload)
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
            if attempt >= max_retries:
                raise
            await asyncio.sleep(min(2.0**attempt, 10.0))
            continue
        if response.status_code == 429:
            retry_after = 1.0
            try:
                retry_after = float(response.json().get("retry_after") or retry_after)
            except Exception:
                header_value = response.headers.get("retry-after")
                if header_value:
                    try:
                        retry_after = float(header_value)
                    except ValueError:
                        retry_after = 1.0
            if attempt >= max_retries:
                response.raise_for_status()
            await asyncio.sleep(min(max(retry_after, 0.25), 10.0))
            continue
        if 500 <= response.status_code < 600 and attempt < max_retries:
            await asyncio.sleep(min(2.0**attempt, 10.0))
            continue
        break
    response.raise_for_status()
    thread_id = str(response.json().get("id") or "")
    if not thread_id:
        raise NewsletterPostConfigError("Discord forum thread creation returned no thread id")
    return thread_id


async def run() -> None:
    _load_dotenv(Path.cwd() / ".env")
    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not token:
        raise NewsletterPostConfigError("missing required env var: DISCORD_BOT_TOKEN")
    channel_raw = os.environ.get("DISCORD_CARD_NEWS_CHANNEL_ID", DEFAULT_CARD_NEWS_CHANNEL_ID).strip()
    os.environ["DISCORD_CARD_NEWS_CHANNEL_ID"] = channel_raw
    channel_id = _required_snowflake("DISCORD_CARD_NEWS_CHANNEL_ID")
    source_raw = os.environ.get("DISCORD_CARD_NEWS_SOURCE", "").strip()
    source = Path(source_raw).expanduser() if source_raw else _latest_archive_path()
    max_cards = int(os.environ.get("DISCORD_CARD_NEWS_MAX_CARDS", "8"))
    hero_image_raw = os.environ.get("DISCORD_CARD_NEWS_HERO_IMAGE_PATH", "").strip()
    hero_image_path = Path(hero_image_raw).expanduser() if hero_image_raw else None
    purge_previous = os.environ.get("DISCORD_PURGE_PREVIOUS_CARD_NEWS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    payload = _load_archive(source)
    trust_summary = run_publication_trust_gate(source, surface="card-news")
    enrich_public_urls = os.environ.get("DISCORD_CARD_NEWS_ENRICH_PUBLIC_URLS", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    enrich_limit = int(os.environ.get("DISCORD_CARD_NEWS_ENRICH_LIMIT", "80"))
    headers = {"Authorization": f"Bot {token}"}
    quality_gate = _card_news_quality_gate_config_from_env()
    side_effect_stage = "not_started"
    evaluation: dict[str, Any] = {}
    cards: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30) as client:
        if enrich_public_urls:
            payload = await enrich_public_metadata(payload, client, max_items=enrich_limit)
        items = [item for item in payload.get("items", []) if isinstance(item, dict)]
        cards = _select_cards(items, max_cards=max_cards)
        audit_path = quality_gate.audit_path or _default_card_news_audit_path()
        if quality_gate.enabled:
            previous_identities, previous_content_signatures = _load_recent_published_card_history(
                audit_path,
                history_days=quality_gate.history_days,
            )
            previous_agent_contexts = _load_recent_published_agent_contexts(
                audit_path,
                history_days=quality_gate.history_days,
            )
            agent_repeated_indices = await _agent_duplicate_indices(cards, previous_agent_contexts, quality_gate)
            evaluation = _evaluate_card_news_quality(
                cards,
                previous_identities,
                quality_gate,
                previous_content_signatures,
                agent_repeated_indices,
            )
            if evaluation["decision"] == "skip":
                record = _build_card_news_audit_record(
                    decision="skip",
                    payload=payload,
                    source=source,
                    cards=cards,
                    evaluation=evaluation,
                )
                _append_card_news_audit(audit_path, record)
                ops_thread_id = ""
                try:
                    ops_thread_id = await _post_card_news_skip_ops_report(
                        client,
                        payload=payload,
                        source=source,
                        evaluation=evaluation,
                        audit_path=audit_path,
                    )
                except (NewsletterPostConfigError, httpx.HTTPError) as ops_exc:
                    print(f"card news skip ops report failed: {ops_exc}", file=sys.stderr)
                counts = evaluation["counts"]
                ops_suffix = f" ops_thread={ops_thread_id}" if ops_thread_id else ""
                print(
                    "skipped card news quality_gate "
                    f"reason={','.join(evaluation['reason_codes'])} "
                    f"source={_source_ref(source, payload)} selected={counts['selected']} "
                    f"new={counts['new']} overlap={counts['overlap_ratio']} audit={audit_path}{ops_suffix}"
                )
                return
        else:
            evaluation = {
                "decision": "publish",
                "reason_codes": ["quality_gate_disabled"],
                "thresholds": _quality_thresholds(quality_gate),
                "counts": {
                    "selected": len(cards),
                    "publishable": sum(1 for item in cards if _publishable_card(item)),
                    "evidence": sum(1 for item in cards if _has_card_evidence(item)),
                    "new": len(cards),
                    "repeated": 0,
                    "overlap_ratio": 0.0,
                },
            }
        messages = _render_card_news_messages_from_cards(payload, cards, item_count=len(items))
        purged = 0
        thread_id = ""
        try:
            side_effect_stage = "channel_lookup"
            channel_response = await client.get(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
            channel_response.raise_for_status()
            channel_data = channel_response.json()
            channel_type = int(channel_data.get("type", 0))
            guild_id = str(channel_data.get("guild_id") or "")
            target_channel_id = channel_id
            if channel_type in FORUM_CHANNEL_TYPES:
                forum_url = f"https://discord.com/api/v10/channels/{channel_id}"
                if purge_previous and guild_id:
                    side_effect_stage = "purge_threads"
                    purged = await _purge_previous_card_news_threads(
                        client,
                        f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                        headers=headers,
                    )
                header_chunks = _split_discord_content(messages[0])
                side_effect_stage = "create_thread"
                thread_id = await _create_forum_card_news_thread(
                    client,
                    forum_url,
                    headers=headers,
                    name=f"{clean_text(payload.get('date') or date.today().isoformat())} 기술 브리핑 카드뉴스",
                    content=header_chunks[0],
                    hero_image_path=hero_image_path,
                )
                target_channel_id = int(thread_id)
                messages_to_post = [*header_chunks[1:], *messages[1:]]
            else:
                url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
                if purge_previous:
                    side_effect_stage = "purge_messages"
                    purged = await _purge_previous_card_news_messages(client, url, headers=headers)
                messages_to_post = [chunk for message in messages for chunk in _split_discord_content(message)]
            post_url = f"https://discord.com/api/v10/channels/{target_channel_id}/messages"
            side_effect_stage = "post_messages"
            for message in messages_to_post:
                await _post_message_with_rate_limit(
                    client,
                    post_url,
                    headers=headers,
                    content=message,
                    suppress_embeds=True,
                )
        except httpx.HTTPError as exc:
            if quality_gate.enabled:
                failure_record = _build_card_news_audit_record(
                    decision="failure",
                    payload=payload,
                    source=source,
                    cards=cards,
                    evaluation=evaluation,
                    failure_metadata={"stage": side_effect_stage, "error_class": exc.__class__.__name__},
                )
                try:
                    _append_card_news_audit(audit_path, failure_record)
                except OSError as audit_exc:
                    print(f"card news failure audit write failed: {audit_exc}", file=sys.stderr)
            raise
    publish_metadata = {"message_count": len(messages), "purged_count": purged}
    if thread_id:
        publish_metadata["thread_id"] = thread_id
    if quality_gate.enabled:
        publish_record = _build_card_news_audit_record(
            decision="publish",
            payload=payload,
            source=source,
            cards=cards,
            evaluation=evaluation,
            publish_metadata=publish_metadata,
        )
        try:
            _append_card_news_audit(audit_path, publish_record)
        except OSError as exc:
            print(f"card news publish audit write failed: {exc}", file=sys.stderr)
    thread_note = f" thread={thread_id}" if thread_id else ""
    print(
        f"posted card news to channel={channel_id}{thread_note} source={source} "
        f"messages={len(messages)} purged={purged} trust_gate={trust_summary.get('decision')}"
    )


def main() -> None:
    try:
        asyncio.run(run())
    except (NewsletterPostConfigError, PublicationTrustGateError, httpx.HTTPError) as exc:
        print(f"card news post failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
