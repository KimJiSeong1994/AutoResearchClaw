from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback keeps API importable.
    fcntl = None  # type: ignore[assignment]

AGENT_ID = "jiphyeonjeon-miner"
REVIEWER_ID = "jiphyeonjeon-claw"
PENDING_STATUS = "pending_claw_review"
SOURCE_ID = "discord_miner"
ARCHIVE_TARGETS = ("newsletter_archive", "newsletter")

_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", flags=re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;!?)\\]}>'\""
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
_PRIVATE_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}
_PRIVATE_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".lan")

_TRACKING_QUERY_KEYS = {
    "eid",
    "fbclid",
    "gclid",
    "igshid",
    "li",
    "lipi",
    "mc_cid",
    "mc_eid",
    "mid",
    "midsig",
    "midtoken",
    "mkt_tok",
    "ref",
    "source",
    "t",
    "trk",
    "trkemail",
    "utm",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_BLOCKED_HOST_SUFFIXES = (".local", ".localhost")
_ACADEMIC_TECH_HOST_HINTS = (
    "aclanthology.org",
    "ai.googleblog.com",
    "anthropic.com",
    "arxiv.org",
    "deepmind.google",
    "doi.org",
    "engineering.fb.com",
    "github.com",
    "huggingface.co",
    "icml.cc",
    "neurips.cc",
    "openai.com",
    "openreview.net",
    "paperswithcode.com",
    "proceedings.mlr.press",
    "pytorch.org",
    "semanticscholar.org",
    "tensorflow.org",
)
_ACADEMIC_TECH_TERMS = (
    "academic",
    "agent",
    "algorithm",
    "architecture",
    "arxiv",
    "benchmark",
    "cuda",
    "dataset",
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
    "machine learning",
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
_OUT_OF_SCOPE_TERMS = (
    "analytics",
    "career ladder",
    "feed/update",
    "funding",
    "hiring",
    "impressions",
    "job alert",
    "job recommendation",
    "jobs/view",
    "market",
    "notifications",
    "partnership",
    "preferences",
    "pricing",
    "profile views",
    "unsubscribe",
    "weekly stats",
    "구인",
    "노출수",
    "지원하기",
    "채용",
    "프로필 조회",
)
_OUT_OF_SCOPE_HOST_HINTS = (
    "slack.com",
)


@dataclass(frozen=True)
class DiscordLinkMetadata:
    guild_id: int | None = None
    channel_id: int | None = None
    message_id: int | None = None
    user_id: int | None = None


@dataclass(frozen=True)
class MinerRecordResult:
    status: str
    intake_id: str
    url: str
    title: str
    intake_path: Path
    review_queue_path: Path
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def duplicate(self) -> bool:
        return self.status == "duplicate"

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"


def clean_text(value: object, *, limit: int = 500) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def sanitize_url(raw_url: object) -> str:
    """Return a public HTTP(S) URL with secret/tracking query params removed."""

    text = clean_text(raw_url, limit=4096).strip("<>()[]{}")
    text = text.rstrip(_TRAILING_PUNCTUATION)
    if not text:
        return ""

    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    if parsed.username or parsed.password or not _public_host(parsed.hostname):
        return ""

    filtered_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in _SENSITIVE_QUERY_KEYS or key_lower in _TRACKING_QUERY_KEYS:
            continue
        if key_lower.startswith("utm_"):
            continue
        filtered_query.append((key, value))

    safe_query = urlencode(filtered_query, doseq=True)
    host = parsed.hostname.lower() if parsed.hostname else ""
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, safe_query, ""))


def extract_urls(text: object) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_PATTERN.finditer(str(text or "")):
        url = sanitize_url(match.group(0))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _looks_academic_or_technical(*, url: str, title: str = "", note: str = "") -> tuple[bool, str]:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path_parts = [part for part in parsed.path.split("/") if part]
    except ValueError:
        host = ""
        path_parts = []
        parsed = urlsplit("")
    public_url_text = " ".join([host, parsed.path]).lower().replace("-", " ").replace("_", " ")
    text = " ".join([public_url_text, title, note]).lower().replace("-", " ").replace("_", " ")
    if any(host == hint or host.endswith(f".{hint}") for hint in _OUT_OF_SCOPE_HOST_HINTS):
        return False, "학술검색/기술리포트 범위 밖 링크입니다."
    if any(term in text for term in _OUT_OF_SCOPE_TERMS):
        return False, "학술검색/기술리포트 범위 밖 링크입니다."
    if (host == "github.com" or host.endswith(".github.com")) and len(path_parts) >= 2:
        return True, "open_source_repository"
    if any(host == hint or host.endswith(f".{hint}") for hint in _ACADEMIC_TECH_HOST_HINTS):
        return True, "academic_or_technical_host"
    signal_count = sum(1 for term in _ACADEMIC_TECH_TERMS if term in text)
    if signal_count >= 1:
        return True, "academic_or_technical_signal"
    return False, "학술검색/기술리포트 관련 공개 단서가 부족합니다."


def record_miner_link(
    *,
    url: str,
    title: str | None = None,
    note: str | None = None,
    intake_path: Path,
    review_queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
) -> MinerRecordResult:
    safe_url = sanitize_url(url)
    if not safe_url:
        raise ValueError("집현전-광부는 공개 http/https 링크만 수집합니다.")

    intake_id = _intake_id(safe_url)
    safe_title = clean_text(title, limit=180) or _fallback_title(safe_url)
    eligible, reason = _looks_academic_or_technical(url=safe_url, title=safe_title, note=clean_text(note, limit=500))
    if not eligible:
        return MinerRecordResult(
            status="rejected",
            intake_id=intake_id,
            url=safe_url,
            title=safe_title,
            intake_path=intake_path,
            review_queue_path=review_queue_path,
            reason=reason,
        )
    record = _build_record(
        intake_id=intake_id,
        url=safe_url,
        title=safe_title,
        note=note,
        discord=discord or DiscordLinkMetadata(),
        created_at=created_at,
    )

    wrote = False
    with locked_jsonl_paths(intake_path, review_queue_path):
        in_intake = _jsonl_contains_id(intake_path, intake_id)
        in_review = _jsonl_contains_id(review_queue_path, intake_id)
        if not in_intake:
            _append_jsonl_unlocked(intake_path, record)
            wrote = True
        if not in_review:
            _append_jsonl_unlocked(review_queue_path, record)
            wrote = True

    return MinerRecordResult(
        status="accepted" if wrote else "duplicate",
        intake_id=intake_id,
        url=safe_url,
        title=safe_title,
        intake_path=intake_path,
        review_queue_path=review_queue_path,
        reason=reason,
    )


def record_message_links(
    *,
    message_text: str,
    intake_path: Path,
    review_queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
) -> list[MinerRecordResult]:
    return [
        record_miner_link(
            url=url,
            intake_path=intake_path,
            review_queue_path=review_queue_path,
            discord=discord,
            created_at=created_at,
        )
        for url in extract_urls(message_text)
    ]


def render_ack(results: list[MinerRecordResult]) -> str:
    accepted = sum(1 for result in results if result.accepted)
    duplicates = sum(1 for result in results if result.duplicate)
    rejected = sum(1 for result in results if result.rejected)
    if not results:
        return "집현전-광부가 수집할 http/https 링크를 찾지 못했습니다."
    if not accepted and not duplicates:
        reason = next((result.reason for result in results if result.rejected and result.reason), "")
        suffix = f" 사유: {reason}" if reason else ""
        return f"⛏️ 집현전-광부: 학술검색/기술리포트 관련 링크가 없어 {rejected}개를 수집 제외했습니다.{suffix}"
    parts = [f"⛏️ 집현전-광부: 링크 {accepted}개를 집현전-클로 검토 큐에 등록했습니다."]
    if duplicates:
        parts.append(f"중복 {duplicates}개는 기존 검토 큐를 유지했습니다.")
    if rejected:
        parts.append(f"학술검색/기술리포트 범위 밖 {rejected}개는 수집 제외했습니다.")
    parts.append("검토 전에는 뉴스레터 아카이브/뉴스레터에 자동 반영하지 않습니다.")
    return " ".join(parts)


@contextmanager
def locked_jsonl_paths(*paths: Path) -> Iterator[None]:
    """Serialize JSONL readers/writers that share review-workflow state."""

    lock_path = _lock_path(paths)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_fh:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        if isinstance(raw, dict):
            rows.append(raw)
    return rows


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with locked_jsonl_paths(path):
        _append_jsonl_unlocked(path, record)


def _build_record(
    *,
    intake_id: str,
    url: str,
    title: str,
    note: str | None,
    discord: DiscordLinkMetadata,
    created_at: datetime | None,
) -> dict[str, Any]:
    now = created_at or datetime.now(timezone.utc)
    run_at = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    summary = clean_text(note, limit=700)
    return {
        "intake_id": intake_id,
        "agent": AGENT_ID,
        "reviewer": REVIEWER_ID,
        "status": PENDING_STATUS,
        "source": SOURCE_ID,
        "intake_source": "discord",
        "title": title,
        "url": url,
        "summary": summary,
        "published_at": run_at[:10],
        "created_at": run_at,
        "tags": ["discord-link", AGENT_ID, PENDING_STATUS],
        "archive_targets": list(ARCHIVE_TARGETS),
        "review": {
            "owner": REVIEWER_ID,
            "required": True,
            "decision": "pending",
            "newsletter_reflection": "blocked_until_approved",
        },
        "discord": {
            "guild_id": discord.guild_id,
            "channel_id": discord.channel_id,
            "message_id": discord.message_id,
            "user_id": discord.user_id,
        },
    }


def _public_host(host: str | None) -> bool:
    if not host:
        return False
    host_lc = host.rstrip(".").lower()
    if host_lc in _PRIVATE_HOSTS or host_lc.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    try:
        ip = ipaddress.ip_address(host_lc.strip("[]"))
    except ValueError:
        return True
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _intake_id(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"miner_{digest}"


def _fallback_title(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    if not path:
        return parsed.netloc
    candidate = f"{parsed.netloc}/{path}".rstrip("/")
    return clean_text(candidate.replace("-", " ").replace("_", " "), limit=180)


def _jsonl_contains_id(path: Path, intake_id: str) -> bool:
    if not path.exists():
        return False
    try:
        for row in read_jsonl(path):
            if row.get("intake_id") == intake_id:
                return True
    except (OSError, json.JSONDecodeError):
        return False
    return False


def _append_jsonl_unlocked(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _lock_path(paths: tuple[Path, ...]) -> Path:
    if not paths:
        return Path(".jiphyeonjeon-miner-jsonl.lock")
    resolved = [path.expanduser() for path in paths]
    try:
        common = Path(os.path.commonpath([str(path.parent) for path in resolved]))
    except ValueError:
        common = resolved[0].parent
    return common / ".jiphyeonjeon-miner-jsonl.lock"
