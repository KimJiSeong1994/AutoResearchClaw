from __future__ import annotations

import hashlib
import html
import ipaddress
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .youtube_video import (
    YouTubeVideoReport,
    build_unavailable_report,
    fetch_youtube_channel_video_urls,
    fetch_youtube_metadata_report,
    is_youtube_channel_url,
    is_youtube_url,
    parse_youtube_url,
    sanitize_content_analysis,
)

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
    "deeplearning.ai",
    "doi.org",
    "engineering.fb.com",
    "github.com",
    "huggingface.co",
    "icml.cc",
    "neurips.cc",
    "openai.com",
    "openreview.net",
    "nature.com",
    "paperswithcode.com",
    "proceedings.mlr.press",
    "pytorch.org",
    "semanticscholar.org",
    "tensorflow.org",
    "alphaxiv.org",
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
_COLLECTION_EXPANSION_MAX_LINKS = int(os.environ.get("JIPHYEONJEON_MINER_COLLECTION_EXPANSION_MAX_LINKS", "10"))
_COLLECTION_FETCH_TIMEOUT_SEC = float(os.environ.get("JIPHYEONJEON_MINER_COLLECTION_FETCH_TIMEOUT_SEC", "12"))
_COLLECTION_USER_AGENT = "jiphyeonjeon-miner-link-expander/0.1"


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


class _SafeRedirectHandler(HTTPRedirectHandler):
    """Reject every 3xx Location whose host is non-public before following.

    Defends against SSRF where a public-looking seed/article URL responds with
    a redirect to a private host (e.g. AWS instance metadata at 169.254.169.254
    or RFC1918 ranges). The default HTTPRedirectHandler only checks scheme; it
    has no host-allowlist.

    NOTE: an earlier version of this guard rebuilt the redirect target through
    sanitize_url, but that helper also strips tracking query parameters and
    forces lowercasing. Some upstreams (notably Nature's collection pages) add
    a session-style query param on every redirect — sanitize_url removed it,
    the upstream re-issued the same redirect on the next hop, and urllib aborted
    with "redirect would lead to an infinite loop". Now we validate the host
    only and pass the original Location through untouched.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        try:
            parsed = urlsplit(newurl)
        except ValueError:
            raise HTTPError(req.full_url, code, "redirect blocked: malformed target", headers, fp)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise HTTPError(req.full_url, code, "redirect blocked: non-http(s) scheme", headers, fp)
        if parsed.username or parsed.password:
            raise HTTPError(req.full_url, code, "redirect blocked: userinfo in URL", headers, fp)
        if not _public_host(parsed.hostname):
            raise HTTPError(req.full_url, code, "redirect blocked: non-public host", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _safe_url_open(url: str, *, timeout: float, user_agent: str):
    """Open *url* with redirect targets host-validated by ``_SafeRedirectHandler``.

    Only the host is checked (sanitize_url is intentionally NOT used on
    redirect — see commit 42c51c0); query parameters pass through verbatim
    so upstreams that round-trip a session token via 302 do not loop.
    """
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html"})
    opener = build_opener(_SafeRedirectHandler())
    return opener.open(request, timeout=timeout)


def _fetch_public_html(url: str) -> str:
    with _safe_url_open(url, timeout=_COLLECTION_FETCH_TIMEOUT_SEC, user_agent=_COLLECTION_USER_AGENT) as response:
        content_type = response.headers.get("Content-Type", "")
        if content_type and "html" not in content_type.lower():
            return ""
        return response.read(800_000).decode("utf-8", "replace")


def _html_links(base_url: str, html_text: str) -> list[str]:
    links: list[str] = []
    seen: set[str] = set()
    for raw_href in re.findall(r"""href=["']([^"']+)["']""", html_text, flags=re.IGNORECASE):
        href = html.unescape(raw_href).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        url = sanitize_url(urljoin(base_url, href))
        if not url or url in seen:
            continue
        seen.add(url)
        links.append(url)
    return links


def _is_alphaxiv_collection(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host == "alphaxiv.org" and parsed.path.rstrip("/") in {"", "/"}


def _is_the_batch_collection(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host == "deeplearning.ai" and parsed.path.rstrip("/") == "/the-batch"


def _is_nature_articles_collection(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return (
        host == "nature.com"
        and parsed.path.rstrip("/") == "/nature/articles"
        and query.get("type") == "article"
    )


def expand_collection_links(url: str) -> list[str]:
    """Expand supported public index pages into bounded post/paper links."""

    safe_url = sanitize_url(url)
    if not safe_url:
        return []
    if _COLLECTION_EXPANSION_MAX_LINKS < 1:
        return []

    if _is_the_batch_collection(safe_url):
        safe_url = urlunsplit(("https", "www.deeplearning.ai", "/the-batch", "", ""))

    if not (
        _is_alphaxiv_collection(safe_url)
        or _is_the_batch_collection(safe_url)
        or _is_nature_articles_collection(safe_url)
    ):
        return []

    try:
        html_text = _fetch_public_html(safe_url)
    except Exception:
        return []

    expanded: list[str] = []
    seen: set[str] = set()
    for link in _html_links(safe_url, html_text):
        parsed = urlsplit(link)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = parsed.path.rstrip("/")
        keep = False
        if _is_alphaxiv_collection(safe_url):
            keep = host == "alphaxiv.org" and path.startswith("/abs/") and len(path.split("/")) >= 3
        elif _is_the_batch_collection(safe_url):
            keep = host == "deeplearning.ai" and re.fullmatch(r"/the-batch/issue-\d+", path) is not None
        elif _is_nature_articles_collection(safe_url):
            keep = host == "nature.com" and re.fullmatch(r"/articles/[a-z]\d{4,5}-\d{3}-\d{4,5}-[\w-]+", path) is not None
        if not keep:
            continue
        canonical = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))
        if canonical in seen:
            continue
        seen.add(canonical)
        expanded.append(canonical)
        if len(expanded) >= _COLLECTION_EXPANSION_MAX_LINKS:
            break
    return expanded


def _expand_or_keep_url(url: str) -> list[str]:
    expanded = expand_collection_links(url)
    return expanded or [url]


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
    summary: str | None = None,
    published_at: str | None = None,
    context_text: str | None = None,
    intake_path: Path,
    review_queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
) -> MinerRecordResult:
    safe_url = sanitize_url(url)
    if not safe_url:
        raise ValueError("집현전-광부는 공개 http/https 링크만 수집합니다.")

    if is_youtube_url(safe_url) and parse_youtube_url(str(url)) is None and parse_youtube_url(safe_url) is None:
        intake_id = _intake_id(safe_url)
        return MinerRecordResult(
            status="rejected",
            intake_id=intake_id,
            url=safe_url,
            title=clean_text(title, limit=180) or _fallback_title(safe_url),
            intake_path=intake_path,
            review_queue_path=review_queue_path,
            reason="unsupported_youtube_url: video URL만 수집합니다.",
        )

    youtube_report = _youtube_report_for_intake(str(url) if is_youtube_url(safe_url) else safe_url)
    if youtube_report is not None:
        safe_url = youtube_report.identity.canonical_url
    intake_id = _intake_id(safe_url)
    safe_title = clean_text(title, limit=180) or clean_text(youtube_report.title if youtube_report else "", limit=180) or _fallback_title(safe_url)
    gate_note = " ".join(
        part
        for part in [
            clean_text(note, limit=500),
            clean_text(context_text, limit=700),
            clean_text(summary, limit=700),
            clean_text(youtube_report.description if youtube_report else "", limit=900),
            clean_text(youtube_report.channel_title if youtube_report else "", limit=180),
        ]
        if part
    )
    eligible, reason = _looks_academic_or_technical(url=safe_url, title=safe_title, note=gate_note)
    if youtube_report is not None and not eligible:
        no_provider = youtube_report.analysis_status == "metadata_unavailable"
        operator_signal, operator_reason = _looks_academic_or_technical(
            url="https://example.invalid/youtube-video",
            title=clean_text(title, limit=180),
            note=" ".join(part for part in [clean_text(note, limit=500), clean_text(context_text, limit=700)] if part),
        )
        if no_provider and operator_signal:
            eligible, reason = True, f"youtube_{youtube_report.analysis_status}:{operator_reason}"
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
        summary=summary or _youtube_summary(youtube_report),
        published_at=published_at or (youtube_report.published_at if youtube_report else None),
        discord=discord or DiscordLinkMetadata(),
        created_at=created_at,
        youtube_report=youtube_report,
        operator_context=context_text,
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
    channel_max_videos: int | None = None,
) -> list[MinerRecordResult]:
    results: list[MinerRecordResult] = []
    seen: set[str] = set()
    for url in extract_urls(message_text):
        for candidate_url in _expand_or_keep_url(url):
            safe_url = sanitize_url(candidate_url)
            if not safe_url or safe_url in seen:
                continue
            seen.add(safe_url)
            results.extend(
                _record_channel_or_link(
                    url=safe_url,
                    context_text=message_text,
                    intake_path=intake_path,
                    review_queue_path=review_queue_path,
                    discord=discord,
                    created_at=created_at,
                    channel_max_videos=channel_max_videos,
                )
            )
    return results


def record_requested_links(
    *,
    url: str,
    title: str | None = None,
    note: str | None = None,
    intake_path: Path,
    review_queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
    channel_max_videos: int | None = None,
) -> list[MinerRecordResult]:
    results: list[MinerRecordResult] = []
    for candidate_url in _expand_or_keep_url(url):
        results.extend(
            _record_channel_or_link(
                url=candidate_url,
                title=title if candidate_url == url else None,
                note=note,
                intake_path=intake_path,
                review_queue_path=review_queue_path,
                discord=discord,
                created_at=created_at,
                channel_max_videos=channel_max_videos,
            )
        )
    return results


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


def _youtube_report_for_intake(url: str) -> YouTubeVideoReport | None:
    if not is_youtube_url(url):
        return None
    if parse_youtube_url(url) is None:
        return None
    return fetch_youtube_metadata_report(url) or build_unavailable_report(url)


def _youtube_summary(report: YouTubeVideoReport | None) -> str | None:
    if report is None:
        return None
    if report.summary_lines:
        return " ".join(report.summary_lines)[:700]
    if report.analysis_status == "metadata_unavailable":
        return "YouTube 영상 링크를 감지했지만 provider key가 없어 metadata_unavailable 상태로 검토 큐에 보냅니다."
    if report.title or report.description:
        return f"YouTube 영상 `{report.title or report.identity.video_id}`의 공개 메타데이터를 수집했습니다. {report.description}"[:700]
    return None


def _youtube_channel_max_videos(override: int | None = None) -> int:
    if override is not None:
        value = override
    else:
        try:
            value = int(os.environ.get("JIPHYEONJEON_MINER_YOUTUBE_CHANNEL_MAX_VIDEOS", "5"))
        except ValueError:
            value = 5
    return max(1, min(25, value))


def _record_channel_or_link(
    *,
    url: str,
    title: str | None = None,
    note: str | None = None,
    context_text: str | None = None,
    intake_path: Path,
    review_queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
    channel_max_videos: int | None = None,
) -> list[MinerRecordResult]:
    safe_url = sanitize_url(url)
    if safe_url and is_youtube_channel_url(safe_url):
        max_results = _youtube_channel_max_videos(channel_max_videos)
        result = fetch_youtube_channel_video_urls(safe_url, max_results=max_results)
        if result is None or result.status != "ready":
            intake_id = _intake_id(safe_url)
            reason = f"youtube_channel_{result.status if result else 'unsupported'}:{result.reason if result else 'unsupported_channel_url'}"
            return [
                MinerRecordResult(
                    status="rejected",
                    intake_id=intake_id,
                    url=safe_url,
                    title=clean_text(title, limit=180) or _fallback_title(safe_url),
                    intake_path=intake_path,
                    review_queue_path=review_queue_path,
                    reason=reason,
                )
            ]
        channel_note = " ".join(
            part
            for part in [
                clean_text(note, limit=500),
                clean_text(context_text, limit=700),
                f"YouTube channel collection source: {result.channel.canonical_url}",
            ]
            if part
        )
        return [
            record_miner_link(
                url=video_url,
                title=None,
                note=channel_note,
                context_text=context_text,
                intake_path=intake_path,
                review_queue_path=review_queue_path,
                discord=discord,
                created_at=created_at,
            )
            for video_url in result.video_urls
        ]
    return [
        record_miner_link(
            url=url,
            title=title,
            note=note,
            context_text=context_text,
            intake_path=intake_path,
            review_queue_path=review_queue_path,
            discord=discord,
            created_at=created_at,
        )
    ]


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
    summary: str | None = None,
    published_at: str | None = None,
    discord: DiscordLinkMetadata,
    created_at: datetime | None,
    youtube_report: YouTubeVideoReport | None = None,
    operator_context: str | None = None,
) -> dict[str, Any]:
    now = created_at or datetime.now(timezone.utc)
    run_at = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    summary_text = clean_text(summary, limit=700) if summary is not None else clean_text(note, limit=700)
    published = clean_text(published_at, limit=40) or run_at[:10]
    record: dict[str, Any] = {
        "intake_id": intake_id,
        "agent": AGENT_ID,
        "reviewer": REVIEWER_ID,
        "status": PENDING_STATUS,
        "source": SOURCE_ID,
        "intake_source": "discord",
        "title": title,
        "url": url,
        "summary": summary_text,
        "published_at": published,
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
    if youtube_report is not None:
        record.update(youtube_report.to_record_fields())
        content_analysis = youtube_report.content_analysis(
            operator_note=clean_text(note, limit=500),
            operator_context=clean_text(operator_context, limit=700),
        )
        if content_analysis:
            record["content_analysis"] = sanitize_content_analysis(content_analysis)
        tags = list(record.get("tags", []))
        tags.extend(["youtube-video", f"youtube:{youtube_report.analysis_status}"])
        record["tags"] = list(dict.fromkeys(str(tag) for tag in tags if str(tag)))
    return record


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
