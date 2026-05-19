"""YouTube URL identity, metadata, and report helpers for Miner intake.

The module intentionally avoids transcript scraping, audiovisual downloads, and
raw provider payload persistence.  It exposes deterministic parsing and a small
optional YouTube Data API metadata path for L0 reports.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
_DEFAULT_PARTS = ("snippet", "contentDetails", "status")
_DEFAULT_TTL_DAYS = 30
_MAX_DESCRIPTION_CHARS = 900
_CONTENT_ANALYSIS_VERSION = "youtube_content_analysis_v1"
_FORBIDDEN_CONTENT_KEYS = {
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
_SENSITIVE_VALUE_RE = re.compile(
    r"\b(?:token|access[ _-]?token|refresh[ _-]?token|relay[ _-]?token|api[ _-]?key|key|auth|password|code|signature|sig|secret|credential)\b\s*[:=]",
    flags=re.IGNORECASE,
)
_FORBIDDEN_CONTENT_MARKER_RE = re.compile(
    r"(?:raw_provider_payload|raw_transcript|caption_text|raw_caption|audio_bytes|audio_path|video_bytes|access_token|refresh_token|private_body)\s*[:=]",
    flags=re.IGNORECASE,
)
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
_CONTENT_ANALYSIS_ALLOWED_STATUSES = {"ready", "unavailable", "blocked", "error", "shadow"}
_CONTENT_ANALYSIS_ALLOWED_TIERS = {
    "metadata_only",
    "operator_note",
    "model_public_youtube_av_no_raw",
    "official_caption_ephemeral",
    "official_caption_unavailable",
}
_LEGACY_EVIDENCE_TIERS = {
    "gemini_youtube_uri_no_transcript": "model_public_youtube_av_no_raw",
    "model_youtube_uri_no_transcript": "model_public_youtube_av_no_raw",
}
_CAPTION_REQUIRED_SCOPES = {
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtubepartner",
}
_CAPTION_LIST_QUOTA_UNITS = 50
_CAPTION_DOWNLOAD_QUOTA_UNITS = 200
_CAPTION_MIN_QUOTA_UNITS = _CAPTION_LIST_QUOTA_UNITS + _CAPTION_DOWNLOAD_QUOTA_UNITS


@dataclass(frozen=True)
class YouTubeVideoIdentity:
    video_id: str
    canonical_url: str
    original_url: str
    start_seconds: int | None = None
    playlist_id: str = ""


@dataclass(frozen=True)
class YouTubeChannelIdentity:
    channel_id: str = ""
    handle: str = ""
    username: str = ""
    canonical_url: str = ""
    original_url: str = ""


@dataclass(frozen=True)
class YouTubeChannelVideosResult:
    status: str
    channel: YouTubeChannelIdentity
    video_urls: tuple[str, ...] = ()
    reason: str = ""
    quota_units: int = 0


@dataclass(frozen=True)
class YouTubeVideoReport:
    identity: YouTubeVideoIdentity
    title: str = ""
    description: str = ""
    channel_title: str = ""
    duration: str = ""
    published_at: str = ""
    provider: str = "none"
    parts: tuple[str, ...] = ()
    etag: str = ""
    metadata_provenance: str = "none"
    analysis_provenance: str = "none"
    analysis_status: str = "metadata_unavailable"
    confidence: float = 0.0
    fetched_at: str = ""
    expires_at: str = ""
    quota_units: int = 0
    summary_lines: tuple[str, ...] = field(default_factory=tuple)

    def media(self) -> dict[str, object]:
        return sanitize_media(
            {
                "type": "video",
                "platform": "youtube",
                "video_id": self.identity.video_id,
                "canonical_url": self.identity.canonical_url,
                "original_url": self.identity.original_url,
                "start_seconds": self.identity.start_seconds,
                "playlist_id": self.identity.playlist_id,
                "channel_title": self.channel_title,
                "duration": self.duration,
                "published_at": self.published_at,
                "provider": self.provider,
                "parts": list(self.parts),
                "etag": self.etag,
                "metadata_provenance": self.metadata_provenance,
                "analysis_provenance": self.analysis_provenance,
                "analysis_status": self.analysis_status,
                "confidence": self.confidence,
                "fetched_at": self.fetched_at,
                "expires_at": self.expires_at,
                "quota_units": self.quota_units,
            }
        )

    def content_analysis(
        self,
        *,
        operator_note: str = "",
        operator_context: str = "",
    ) -> dict[str, object]:
        return build_content_analysis(
            self,
            operator_note=operator_note,
            operator_context=operator_context,
        )

    def to_record_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {
            "content_type": "video/youtube",
            "media": self.media(),
            "content_analysis": self.content_analysis(),
            "youtube": {
                "video_id": self.identity.video_id,
                "canonical_url": self.identity.canonical_url,
                "analysis_status": self.analysis_status,
            },
        }
        if self.summary_lines:
            fields["summary_lines"] = list(self.summary_lines[:3])
        return fields


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")


def parse_youtube_url(url: str) -> YouTubeVideoIdentity | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if not (host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")):
        return None

    video_id = ""
    query = parse_qs(parsed.query, keep_blank_values=True)
    path_parts = [part for part in parsed.path.split("/") if part]
    if host.endswith("youtu.be") and path_parts:
        video_id = path_parts[0]
    elif parsed.path == "/watch":
        video_id = (query.get("v") or [""])[0]
    elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "embed", "live"}:
        video_id = path_parts[1]

    video_id = video_id.strip()
    if not _VIDEO_ID_RE.match(video_id):
        return None
    start_seconds = extract_start_seconds(url)
    playlist_id = ((query.get("list") or [""])[0] or "")[:80]
    return YouTubeVideoIdentity(
        video_id=video_id,
        canonical_url=canonical_youtube_url(video_id),
        original_url=_sanitized_original_url(parsed, video_id=video_id, start_seconds=start_seconds, playlist_id=playlist_id),
        start_seconds=start_seconds,
        playlist_id=playlist_id,
    )


def parse_youtube_channel_url(url: str) -> YouTubeChannelIdentity | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if not (host in _YOUTUBE_HOSTS or host.endswith(".youtube.com")):
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        return None
    if path_parts[0].startswith("@") and len(path_parts[0]) > 1:
        handle = path_parts[0][:80]
        return YouTubeChannelIdentity(handle=handle, canonical_url=f"https://www.youtube.com/{handle}", original_url=f"https://www.youtube.com/{handle}")
    if len(path_parts) >= 2 and path_parts[0] == "channel" and path_parts[1].startswith("UC"):
        channel_id = path_parts[1][:80]
        return YouTubeChannelIdentity(
            channel_id=channel_id,
            canonical_url=f"https://www.youtube.com/channel/{channel_id}",
            original_url=f"https://www.youtube.com/channel/{channel_id}",
        )
    if len(path_parts) >= 2 and path_parts[0] == "user":
        username = path_parts[1][:80]
        return YouTubeChannelIdentity(username=username, canonical_url=f"https://www.youtube.com/user/{username}", original_url=f"https://www.youtube.com/user/{username}")
    return None


def is_youtube_channel_url(url: str) -> bool:
    return parse_youtube_channel_url(url) is not None and parse_youtube_url(url) is None


def _sanitized_original_url(parsed, *, video_id: str, start_seconds: int | None, playlist_id: str) -> str:
    """Preserve useful YouTube review context without secret/tracking params."""
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/watch"
    query: list[tuple[str, str]] = []
    if path == "/watch" or host.endswith("youtu.be"):
        query.append(("v", video_id))
        path = "/watch"
        host = "www.youtube.com"
    if playlist_id:
        query.append(("list", playlist_id))
    if start_seconds is not None:
        query.append(("t", str(start_seconds)))
    return urlunsplit(("https", host, path, urlencode(query), ""))


def canonical_youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"


def extract_start_seconds(url: str) -> int | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key in ("t", "start"):
        raw = (query.get(key) or [""])[0]
        seconds = _parse_time_seconds(raw)
        if seconds is not None:
            return seconds
    if parsed.fragment:
        frag = parsed.fragment.removeprefix("t=")
        return _parse_time_seconds(frag)
    return None


def _parse_time_seconds(value: str) -> int | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    if text.isdigit():
        return max(0, int(text))
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", text)
    if not match:
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def build_unavailable_report(url: str) -> YouTubeVideoReport | None:
    identity = parse_youtube_url(url)
    if identity is None:
        return None
    fetched_at, expires_at = _timestamps()
    return YouTubeVideoReport(
        identity=identity,
        fetched_at=fetched_at,
        expires_at=expires_at,
        analysis_status="metadata_unavailable",
        analysis_provenance="none",
        metadata_provenance="none",
    )


def fetch_youtube_metadata_report(url: str, *, api_key: str | None = None, timeout_sec: float = 8.0) -> YouTubeVideoReport | None:
    """Fetch sanitized L0 metadata with YouTube Data API when a key is configured.

    Returns a metadata_unavailable report when the URL is YouTube but no key is
    present. Returns ``None`` for non-video/channel URLs.
    """
    identity = parse_youtube_url(url)
    if identity is None:
        return None
    key = api_key if api_key is not None else os.environ.get("YOUTUBE_DATA_API_KEY", "")
    if not key:
        return build_unavailable_report(url)

    params = urlencode(
        {
            "key": key,
            "id": identity.video_id,
            "part": ",".join(_DEFAULT_PARTS),
            "fields": "items(etag,snippet(title,description,channelTitle,publishedAt),contentDetails(duration),status(privacyStatus,embeddable))",
        }
    )
    endpoint = f"https://www.googleapis.com/youtube/v3/videos?{params}"
    fetched_at, expires_at = _timestamps()
    try:
        req = Request(endpoint, headers={"User-Agent": "jiphyeonjeon-miner-youtube/0.1"})
        with urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310 - fixed official API endpoint.
            payload = json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
    except Exception:
        return YouTubeVideoReport(
            identity=identity,
            provider="youtube_data_api",
            parts=_DEFAULT_PARTS,
            metadata_provenance="youtube_data_api_v3_videos_list",
            analysis_status="metadata_unavailable",
            fetched_at=fetched_at,
            expires_at=expires_at,
            quota_units=1,
        )
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return YouTubeVideoReport(
            identity=identity,
            provider="youtube_data_api",
            parts=_DEFAULT_PARTS,
            metadata_provenance="youtube_data_api_v3_videos_list",
            analysis_status="rejected",
            fetched_at=fetched_at,
            expires_at=expires_at,
            quota_units=1,
        )
    item = items[0] if isinstance(items[0], dict) else {}
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    content = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    title = _clean(snippet.get("title"), limit=180)
    description = _clean(snippet.get("description"), limit=_MAX_DESCRIPTION_CHARS)
    channel = _clean(snippet.get("channelTitle"), limit=180)
    published = _clean(snippet.get("publishedAt"), limit=40)
    duration = _clean(content.get("duration"), limit=40)
    lines = _summary_lines(title=title, description=description, channel=channel)
    privacy_status = _clean(status.get("privacyStatus"), limit=40).lower()
    embeddable = status.get("embeddable")
    unavailable = privacy_status and privacy_status != "public" or embeddable is False
    return YouTubeVideoReport(
        identity=identity,
        title=title if not unavailable else "",
        description=description if not unavailable else "",
        channel_title=channel if not unavailable else "",
        duration=duration if not unavailable else "",
        published_at=(published[:10] if len(published) >= 10 else published) if not unavailable else "",
        provider="youtube_data_api",
        parts=_DEFAULT_PARTS,
        etag=_clean(item.get("etag"), limit=120),
        metadata_provenance="youtube_data_api_v3_videos_list",
        analysis_provenance="metadata_only" if not unavailable else "none",
        analysis_status="metadata_ready" if not unavailable else "metadata_unavailable",
        confidence=0.55 if not unavailable else 0.0,
        fetched_at=fetched_at,
        expires_at=expires_at,
        quota_units=1,
        summary_lines=tuple(lines if not unavailable else ()),
    )


def build_content_analysis(
    report: YouTubeVideoReport,
    *,
    operator_note: str = "",
    operator_context: str = "",
) -> dict[str, object]:
    """Build a derived-only content analysis skeleton for a YouTube report.

    The output deliberately stores only summaries/claims/labels.  It never
    persists raw captions, transcripts, audio/video bytes, or raw provider
    payloads; ``sanitize_content_analysis`` enforces that boundary again before
    returning the report.
    """

    note = _clean(operator_note, limit=500)
    context = _clean(operator_context, limit=700)
    operator_text = _clean(" ".join(part for part in (note, context) if part), limit=700)
    operator_used = bool(operator_text)
    if operator_used:
        evidence_tier = "operator_note"
        analysis_provenance = "operator_note"
        provider = "operator"
        source_separation = "operator_note"
        status = "ready"
        confidence = max(float(report.confidence or 0.0), 0.35)
        summary_lines = [
            "운영자 메모 기준 YouTube 영상 검토 리포트입니다.",
            "운영자 메모/주변 Discord 문맥만 요약 근거로 사용하며 provider transcript처럼 표현하지 않습니다.",
            operator_text,
        ]
        claims = [{"text": operator_text, "basis": "operator_note", "confidence": min(confidence, 0.55)}]
        limitations = [
            "raw transcript/audio/video는 저장하지 않음",
            "운영자 메모 기준이며 공식 caption/transcript 근거 아님",
        ]
        fallback_reason = "provider_metadata_unavailable_operator_note_used" if report.analysis_status == "metadata_unavailable" else ""
    else:
        evidence_tier = "metadata_only"
        analysis_provenance = "metadata_only" if report.analysis_provenance in {"metadata_only", ""} else report.analysis_provenance
        provider = report.provider or "none"
        source_separation = "metadata"
        status = "ready" if report.analysis_status == "metadata_ready" else "unavailable"
        confidence = float(report.confidence or (0.15 if status == "unavailable" else 0.45))
        summary_lines = list(report.summary_lines) or [
            "공개 메타데이터 기준 YouTube 영상 검토 리포트입니다.",
            "자막/transcript 근거 아님; raw transcript/audio/video는 저장하지 않습니다.",
            f"영상 ID {report.identity.video_id}의 metadata 상태: {report.analysis_status}",
        ]
        claims = []
        if report.title:
            claims.append({"text": f"공개 제목: {report.title}", "basis": "metadata.title", "confidence": min(confidence, 0.6)})
        if report.channel_title:
            claims.append(
                {
                    "text": f"공개 채널: {report.channel_title}",
                    "basis": "metadata.channel_title",
                    "confidence": min(confidence, 0.6),
                }
            )
        limitations = [
            "공개 메타데이터 기준이며 영상 발화/직접 인용으로 해석하지 않음",
            "raw transcript/audio/video는 저장하지 않음",
            "자막/transcript 근거 아님",
        ]
        fallback_reason = "youtube_metadata_unavailable" if status == "unavailable" else ""

    return sanitize_content_analysis(
        {
            "version": _CONTENT_ANALYSIS_VERSION,
            "analysis_status": status,
            "evidence_tier": evidence_tier,
            "analysis_provenance": analysis_provenance,
            "provider": provider,
            "summary_lines": summary_lines,
            "claims": claims,
            "limitations": limitations,
            "quota_units": report.quota_units,
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "operator_note_used": operator_used,
            "source_separation": source_separation,
            "fetched_at": report.fetched_at,
            "expires_at": report.expires_at,
            "fallback_reason": fallback_reason,
            "policy_flags": ["no_raw_transcript_persisted", "no_audio_video_persisted"],
        }
    )


def fetch_youtube_channel_video_urls(
    url: str,
    *,
    api_key: str | None = None,
    max_results: int = 5,
    timeout_sec: float = 8.0,
) -> YouTubeChannelVideosResult | None:
    """Resolve a YouTube channel URL to recent upload video URLs via official API.

    The function uses documented YouTube Data API surfaces only:
    ``channels.list`` to resolve the uploads playlist, then ``playlistItems.list``
    to list recent uploads. It performs no scraping and stores no provider
    payload.
    """

    channel = parse_youtube_channel_url(url)
    if channel is None:
        return None
    key = api_key if api_key is not None else os.environ.get("YOUTUBE_DATA_API_KEY", "")
    if not key:
        return YouTubeChannelVideosResult(status="unavailable", channel=channel, reason="missing_youtube_data_api_key")
    max_results = max(1, min(25, int(max_results or 5)))
    params: dict[str, str] = {
        "key": key,
        "part": "contentDetails",
        "fields": "items(id,contentDetails(relatedPlaylists(uploads)))",
        "maxResults": "1",
    }
    if channel.channel_id:
        params["id"] = channel.channel_id
    elif channel.handle:
        params["forHandle"] = channel.handle
    elif channel.username:
        params["forUsername"] = channel.username
    else:
        return YouTubeChannelVideosResult(status="unsupported", channel=channel, reason="unsupported_channel_identifier")
    quota_units = 1
    try:
        channel_payload = _fetch_json(f"https://www.googleapis.com/youtube/v3/channels?{urlencode(params)}", timeout_sec=timeout_sec)
    except Exception:
        return YouTubeChannelVideosResult(status="error", channel=channel, reason="channels_list_error", quota_units=quota_units)
    items = channel_payload.get("items") if isinstance(channel_payload, dict) else None
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    uploads = (
        first.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads", "")
        if isinstance(first.get("contentDetails"), dict)
        else ""
    )
    resolved_channel_id = _clean(first.get("id") or channel.channel_id, limit=80)
    if not uploads:
        return YouTubeChannelVideosResult(status="unavailable", channel=channel, reason="uploads_playlist_unavailable", quota_units=quota_units)
    playlist_params = urlencode(
        {
            "key": key,
            "part": "contentDetails",
            "playlistId": uploads,
            "maxResults": str(max_results),
            "fields": "items(contentDetails(videoId))",
        }
    )
    quota_units += 1
    try:
        playlist_payload = _fetch_json(f"https://www.googleapis.com/youtube/v3/playlistItems?{playlist_params}", timeout_sec=timeout_sec)
    except Exception:
        return YouTubeChannelVideosResult(status="error", channel=channel, reason="playlist_items_error", quota_units=quota_units)
    playlist_items = playlist_payload.get("items") if isinstance(playlist_payload, dict) else []
    video_ids: list[str] = []
    if isinstance(playlist_items, list):
        for item in playlist_items:
            if not isinstance(item, dict):
                continue
            content = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
            video_id = _clean(content.get("videoId"), limit=40)
            if _VIDEO_ID_RE.match(video_id):
                video_ids.append(video_id)
    deduped = tuple(dict.fromkeys(canonical_youtube_url(video_id) for video_id in video_ids))
    if not deduped:
        return YouTubeChannelVideosResult(status="empty", channel=channel, reason="no_recent_upload_videos", quota_units=quota_units)
    resolved = YouTubeChannelIdentity(
        channel_id=resolved_channel_id or channel.channel_id,
        handle=channel.handle,
        username=channel.username,
        canonical_url=f"https://www.youtube.com/channel/{resolved_channel_id}" if resolved_channel_id else channel.canonical_url,
        original_url=channel.original_url,
    )
    return YouTubeChannelVideosResult(status="ready", channel=resolved, video_urls=deduped, quota_units=quota_units)


def _fetch_json(url: str, *, timeout_sec: float) -> dict[str, Any]:
    req = Request(url, headers={"User-Agent": "jiphyeonjeon-miner-youtube/0.1"})
    with urlopen(req, timeout=timeout_sec) as resp:  # noqa: S310 - fixed official API endpoint.
        payload = json.loads(resp.read(2_000_000).decode("utf-8", "replace"))
    return payload if isinstance(payload, dict) else {}


def sanitize_content_analysis(content_analysis: Any) -> dict[str, object]:
    """Return a canonical, derived-only YouTube ``content_analysis`` object."""

    if not isinstance(content_analysis, dict):
        return {}
    out: dict[str, object] = {}
    for key in _CONTENT_ANALYSIS_ALLOWED_KEYS:
        if key not in content_analysis:
            continue
        if key.lower() in _FORBIDDEN_CONTENT_KEYS:
            continue
        value = _sanitize_content_value(content_analysis.get(key), key=key)
        if value in (None, "", [], {}):
            continue
        out[key] = value

    out["version"] = _CONTENT_ANALYSIS_VERSION
    tier = _normalize_evidence_tier(str(out.get("evidence_tier") or "metadata_only"))
    if tier not in _CONTENT_ANALYSIS_ALLOWED_TIERS:
        tier = "metadata_only"
    out["evidence_tier"] = tier

    status = _clean(out.get("analysis_status"), limit=40)
    if status not in _CONTENT_ANALYSIS_ALLOWED_STATUSES:
        legacy_status = _clean(content_analysis.get("analysis_status"), limit=40)
        status = "ready" if legacy_status in {"metadata_ready", "ready"} else "unavailable"
    out["analysis_status"] = status

    provenance = _clean(out.get("analysis_provenance"), limit=120) or ("operator_note" if tier == "operator_note" else "metadata_only")
    if provenance in _LEGACY_EVIDENCE_TIERS:
        provenance = "gemini_public_youtube_av_no_raw"
    out["analysis_provenance"] = provenance

    source = _clean(out.get("source_separation"), limit=60)
    if source not in {"metadata", "operator_note", "provider_model", "official_caption"}:
        source = {
            "operator_note": "operator_note",
            "model_public_youtube_av_no_raw": "provider_model",
            "official_caption_ephemeral": "official_caption",
            "official_caption_unavailable": "official_caption",
        }.get(tier, "metadata")
    out["source_separation"] = source
    out["operator_note_used"] = bool(out.get("operator_note_used")) or tier == "operator_note"

    provider = _clean(out.get("provider"), limit=80) or ("operator" if tier == "operator_note" else "none")
    out["provider"] = provider
    if "confidence" in out:
        out["confidence"] = _clamped_float(out["confidence"])
    if "quota_units" in out:
        out["quota_units"] = max(0, int(_clamped_float(out["quota_units"], upper=1_000_000)))
    return out


def sanitize_media(media: Any) -> dict[str, object]:
    if not isinstance(media, dict):
        return {}
    allowed = {
        "type",
        "platform",
        "video_id",
        "canonical_url",
        "original_url",
        "start_seconds",
        "playlist_id",
        "channel_title",
        "duration",
        "published_at",
        "provider",
        "parts",
        "etag",
        "metadata_provenance",
        "analysis_provenance",
        "analysis_status",
        "confidence",
        "fetched_at",
        "expires_at",
        "quota_units",
    }
    out: dict[str, object] = {}
    for key in allowed:
        value = media.get(key)
        if value in (None, "", [], {}):
            continue
        if key == "parts" and isinstance(value, list):
            out[key] = [_clean(v, limit=60) for v in value if _clean(v, limit=60)][:8]
        elif isinstance(value, (int, float, bool)):
            out[key] = value
        elif isinstance(value, str):
            if key == "original_url":
                identity = parse_youtube_url(value)
                if identity is None:
                    continue
                out[key] = identity.original_url
            elif key == "canonical_url":
                identity = parse_youtube_url(value)
                if identity is None:
                    video_id = _clean(media.get("video_id"), limit=40)
                    if not _VIDEO_ID_RE.match(video_id):
                        continue
                    out[key] = canonical_youtube_url(video_id)
                else:
                    out[key] = identity.canonical_url
            else:
                out[key] = _clean(value, limit=500 if key.endswith("url") else 180)
    if out.get("type") != "video" or out.get("platform") != "youtube" or not out.get("video_id"):
        return {}
    return out


def media_to_metadata_tuple(media: dict[str, object]) -> tuple[tuple[str, str], ...]:
    sanitized = sanitize_media(media)
    pairs: list[tuple[str, str]] = []
    for key, value in sorted(sanitized.items()):
        if isinstance(value, list):
            text = ",".join(str(v) for v in value)
        else:
            text = str(value)
        pairs.append((f"media.{key}", _clean(text, limit=500)))
    return tuple(pairs)


def _sanitize_content_value(value: Any, *, key: str = "") -> object | None:
    if key.lower() in _FORBIDDEN_CONTENT_KEYS:
        return None
    if isinstance(value, dict):
        if key == "claims":
            # ``claims`` is schema-defined as a list; dict input is rejected
            # rather than recursively storing unknown provider-shaped payloads.
            return None
        out: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            child_key = _clean(raw_key, limit=80)
            if not child_key or child_key.lower() in _FORBIDDEN_CONTENT_KEYS:
                continue
            child = _sanitize_content_value(raw_value, key=child_key)
            if child not in (None, "", [], {}):
                out[child_key] = child
        return out
    if isinstance(value, (list, tuple)):
        if key == "claims":
            claims: list[dict[str, object]] = []
            for item in value:
                claim = _sanitize_claim(item)
                if claim:
                    claims.append(claim)
                if len(claims) >= 8:
                    break
            return claims
        items: list[object] = []
        limit = 8 if key in {"summary_lines", "limitations", "policy_flags"} else 20
        for item in value:
            sanitized = _sanitize_content_value(item)
            if sanitized in (None, "", [], {}):
                continue
            items.append(sanitized)
            if len(items) >= limit:
                break
        return items
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        if _SENSITIVE_VALUE_RE.search(value) or _FORBIDDEN_CONTENT_MARKER_RE.search(value):
            return None
        return _clean(value, limit=900 if key in {"summary_lines", "limitations"} else 500)
    return None


def _sanitize_claim(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    text = _sanitize_content_value(value.get("text"), key="text")
    basis = _sanitize_content_value(value.get("basis"), key="basis")
    if not isinstance(text, str) or not text:
        return {}
    claim: dict[str, object] = {"text": text}
    if isinstance(basis, str) and basis:
        claim["basis"] = basis
    confidence = value.get("confidence")
    if confidence not in (None, ""):
        claim["confidence"] = _clamped_float(confidence)
    return claim


def _normalize_evidence_tier(value: str) -> str:
    text = _clean(value, limit=80)
    return _LEGACY_EVIDENCE_TIERS.get(text, text)


def _clamped_float(value: object, *, upper: float = 1.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(upper, number))


def build_official_caption_gate_analysis(
    *,
    oauth_scopes: tuple[str, ...] = (),
    has_edit_permission: bool = False,
    quota_budget_units: int = 0,
    caption_track_available: bool = False,
    provider_error_class: str = "",
    fetched_at: str = "",
    expires_at: str = "",
) -> dict[str, object]:
    """Return the official-caption opt-in gate outcome without API calls.

    The real caption provider must pass this gate before calling the official
    ``captions.list`` (50 units) and ``captions.download`` (200 units) APIs.
    This helper deliberately performs no network work and stores no raw caption.
    """

    scope_ok = bool(set(oauth_scopes) & _CAPTION_REQUIRED_SCOPES)
    error_class = _clean(provider_error_class, limit=80)
    if error_class:
        reason = f"caption_provider_error:{error_class}"
    elif not scope_ok:
        reason = "missing_oauth_scope"
    elif not has_edit_permission:
        reason = "missing_caption_edit_permission"
    elif quota_budget_units < _CAPTION_MIN_QUOTA_UNITS:
        reason = "insufficient_caption_quota_budget"
    elif not caption_track_available:
        reason = "caption_track_unavailable"
    else:
        reason = "caption_provider_not_configured"
    return sanitize_content_analysis(
        {
            "version": _CONTENT_ANALYSIS_VERSION,
            "analysis_status": "unavailable",
            "evidence_tier": "official_caption_unavailable",
            "analysis_provenance": "youtube_data_api_v3_captions_list_download_ephemeral",
            "provider": "youtube_data_api",
            "summary_lines": ["공식 caption 분석을 사용할 수 없어 fallback 경로를 사용합니다."],
            "limitations": ["공식 caption 원문은 저장하지 않음", "raw transcript/audio/video는 저장하지 않음"],
            "quota_units": 0,
            "confidence": 0.0,
            "operator_note_used": False,
            "source_separation": "official_caption",
            "fetched_at": fetched_at,
            "expires_at": expires_at,
            "fallback_reason": reason,
            "policy_flags": ["caption_gate_no_api_call", "no_raw_transcript_persisted"],
        }
    )


def build_official_caption_ephemeral_analysis(
    *,
    summary_lines: list[str],
    claims: list[dict[str, object]] | None = None,
    caption_track_id_hash: str = "",
    caption_language: str = "",
    fetched_at: str = "",
    expires_at: str = "",
) -> dict[str, object]:
    """Build a derived-only success artifact for a mocked official caption path."""

    policy_flags = ["ephemeral_deleted=true", "no_raw_transcript_persisted"]
    if caption_track_id_hash:
        policy_flags.append(f"caption_track_id_hash:{_clean(caption_track_id_hash, limit=80)}")
    if caption_language:
        policy_flags.append(f"caption_language:{_clean(caption_language, limit=30)}")
    return sanitize_content_analysis(
        {
            "version": _CONTENT_ANALYSIS_VERSION,
            "analysis_status": "ready",
            "evidence_tier": "official_caption_ephemeral",
            "analysis_provenance": "youtube_data_api_v3_captions_list_download_ephemeral",
            "provider": "youtube_data_api",
            "summary_lines": summary_lines,
            "claims": claims or [],
            "limitations": ["공식 caption 기반 derived summary만 저장; raw caption은 폐기"],
            "quota_units": _CAPTION_MIN_QUOTA_UNITS,
            "confidence": 0.75,
            "operator_note_used": False,
            "source_separation": "official_caption",
            "fetched_at": fetched_at,
            "expires_at": expires_at,
            "policy_flags": policy_flags,
        }
    )


def _timestamps() -> tuple[str, str]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    expires = now + timedelta(days=_DEFAULT_TTL_DAYS)
    return now.isoformat().replace("+00:00", "Z"), expires.isoformat().replace("+00:00", "Z")


def _summary_lines(*, title: str, description: str, channel: str) -> list[str]:
    return [
        f"YouTube 영상 `{title or '제목 미확인'}`의 공개 메타데이터를 수집했습니다.",
        f"채널 `{channel or 'unknown'}`의 공개 제목/설명만 사용하며 transcript 근거는 없습니다.",
        (description[:180] if description else "영상 설명이 없거나 API 메타데이터를 확인하지 못했습니다."),
    ]


def _clean(value: object, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


__all__ = [
    "YouTubeVideoIdentity",
    "YouTubeVideoReport",
    "YouTubeChannelIdentity",
    "YouTubeChannelVideosResult",
    "build_content_analysis",
    "build_official_caption_ephemeral_analysis",
    "build_official_caption_gate_analysis",
    "build_unavailable_report",
    "canonical_youtube_url",
    "extract_start_seconds",
    "fetch_youtube_metadata_report",
    "fetch_youtube_channel_video_urls",
    "is_youtube_url",
    "is_youtube_channel_url",
    "media_to_metadata_tuple",
    "parse_youtube_url",
    "parse_youtube_channel_url",
    "sanitize_content_analysis",
    "sanitize_media",
]
