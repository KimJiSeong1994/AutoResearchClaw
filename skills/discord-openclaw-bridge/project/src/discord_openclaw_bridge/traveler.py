from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .miner import DiscordLinkMetadata, append_jsonl, clean_text, read_jsonl, sanitize_url

AGENT_ID = "jiphyeonjeon-traveler"
REVIEWER_ID = "jiphyeonjeon-claw"
DOWNSTREAM_COLLECTOR_ID = "jiphyeonjeon-miner"
PENDING_STATUS = "pending_source_review"
SOURCE_ID = "discord_traveler"

_ALLOWED_SOURCE_TYPES = {
    "rss",
    "archive_page",
    "newsletter_landing",
    "article_hub",
    "research_lab_blog",
    "engineering_blog",
    "conference_feed",
    "dataset_release_feed",
    "manual_watch",
    "other",
}


@dataclass(frozen=True)
class TravelerRecordResult:
    status: str
    candidate_id: str
    url: str
    title: str
    queue_path: Path
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def duplicate(self) -> bool:
        return self.status == "duplicate"


@dataclass(frozen=True)
class TravelerSourceInput:
    url: str
    title: str | None = None
    source_type: str | None = None
    reliability_note: str | None = None
    cadence_note: str | None = None
    topic_fit: str | None = None
    collection_hint: str | None = None
    access_constraints: str | None = None
    next_action: str | None = None


def _candidate_id(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"traveler_{digest}"


def _fallback_title(url: str) -> str:
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    if not path:
        return parsed.netloc
    return clean_text(f"{parsed.netloc}/{path}".replace("-", " ").replace("_", " "), limit=180)


def _normalize_source_type(value: str | None) -> str:
    normalized = clean_text(value or "other", limit=80).lower().replace(" ", "_").replace("-", "_")
    return normalized if normalized in _ALLOWED_SOURCE_TYPES else "other"


def _jsonl_contains_candidate(path: Path, candidate_id: str) -> bool:
    if not path.exists():
        return False
    try:
        for row in read_jsonl(path):
            if row.get("candidate_id") == candidate_id:
                return True
    except (OSError, json.JSONDecodeError):
        return False
    return False


def build_source_candidate_record(
    source: TravelerSourceInput,
    *,
    queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    safe_url = sanitize_url(source.url)
    if not safe_url:
        raise ValueError("집현전-여행자는 공개 http/https 출처 URL만 기록합니다.")
    now = created_at or datetime.now(timezone.utc)
    created = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_type = _normalize_source_type(source.source_type)
    title = clean_text(source.title, limit=180) or _fallback_title(safe_url)
    discord_meta = discord or DiscordLinkMetadata()
    return {
        "candidate_id": _candidate_id(safe_url),
        "agent": AGENT_ID,
        "reviewer": REVIEWER_ID,
        "downstream_collector": DOWNSTREAM_COLLECTOR_ID,
        "status": PENDING_STATUS,
        "source": SOURCE_ID,
        "source_type": source_type,
        "title": title,
        "url": safe_url,
        "created_at": created,
        "reliability_rationale": clean_text(source.reliability_note, limit=700),
        "update_cadence_evidence": clean_text(source.cadence_note, limit=400),
        "topic_fit": clean_text(source.topic_fit, limit=400),
        "collection_method_hint": clean_text(source.collection_hint or source_type, limit=80),
        "access_constraints": clean_text(source.access_constraints or "public_http", limit=300),
        "recommended_next_action": clean_text(source.next_action or "review_for_miner_seed", limit=300),
        "tags": ["source-discovery", AGENT_ID, PENDING_STATUS, source_type],
        "review": {
            "owner": REVIEWER_ID,
            "required": True,
            "decision": "pending",
            "miner_seed_expansion": "blocked_until_reviewed",
        },
        "discord": {
            "guild_id": discord_meta.guild_id,
            "channel_id": discord_meta.channel_id,
            "message_id": discord_meta.message_id,
            "user_id": discord_meta.user_id,
        },
        "queue_path": str(queue_path),
    }


def record_source_candidate(
    source: TravelerSourceInput,
    *,
    queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
) -> TravelerRecordResult:
    record = build_source_candidate_record(source, queue_path=queue_path, discord=discord, created_at=created_at)
    candidate_id = str(record["candidate_id"])
    if _jsonl_contains_candidate(queue_path, candidate_id):
        return TravelerRecordResult(
            status="duplicate",
            candidate_id=candidate_id,
            url=str(record["url"]),
            title=str(record["title"]),
            queue_path=queue_path,
            reason="already queued",
        )
    append_jsonl(queue_path, record)
    return TravelerRecordResult(
        status="accepted",
        candidate_id=candidate_id,
        url=str(record["url"]),
        title=str(record["title"]),
        queue_path=queue_path,
    )


def render_traveler_ack(result: TravelerRecordResult) -> str:
    if result.duplicate:
        return f"🧭 집현전-여행자: 이미 등록된 출처 후보입니다. `{result.candidate_id}` — {result.title}"
    return (
        f"🧭 집현전-여행자: 출처 후보를 집현전-클로 검토 큐에 기록했습니다. "
        f"`{result.candidate_id}` — {result.title}. "
        "검토 전에는 광부 seed나 뉴스레터에 자동 반영하지 않습니다."
    )


def default_source_queue_path() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH",
            str(Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-traveler" / "source-candidates.jsonl"),
        )
    ).expanduser()

@dataclass(frozen=True)
class TravelerResearchRequest:
    topic: str
    scope: str | None = None
    min_sources_to_review: int = 20
    requester_note: str | None = None


def default_research_queue_path() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH",
            str(Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-traveler" / "research-requests.jsonl"),
        )
    ).expanduser()


def _request_id(topic: str, created: str) -> str:
    digest = hashlib.sha256(f"{topic}\n{created}".encode("utf-8")).hexdigest()[:16]
    return f"traveler_request_{digest}"


def record_research_request(
    request: TravelerResearchRequest,
    *,
    queue_path: Path,
    candidate_queue_path: Path,
    discord: DiscordLinkMetadata | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    topic = clean_text(request.topic, limit=200)
    if not topic:
        raise ValueError("집현전-여행자 리서치 주제를 입력해야 합니다.")
    min_sources = max(10, min(int(request.min_sources_to_review or 20), 80))
    now = created_at or datetime.now(timezone.utc)
    created = now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    discord_meta = discord or DiscordLinkMetadata()
    record = {
        "request_id": _request_id(topic, created),
        "agent": AGENT_ID,
        "status": "pending_deep_research",
        "topic": topic,
        "scope": clean_text(request.scope or "high-trust recurring technical sources", limit=300),
        "min_sources_to_review": min_sources,
        "required_method": "deep_research_compare_many_sources_before_candidate_selection",
        "candidate_queue_path": str(candidate_queue_path),
        "created_at": created,
        "requester_note": clean_text(request.requester_note, limit=700),
        "acceptance_criteria": {
            "review_many_sources": True,
            "minimum_sources_to_review": min_sources,
            "record_rejected_sources": True,
            "candidate_requires_evidence": True,
            "no_single_url_fast_track": True,
        },
        "discord": {
            "guild_id": discord_meta.guild_id,
            "channel_id": discord_meta.channel_id,
            "message_id": discord_meta.message_id,
            "user_id": discord_meta.user_id,
        },
    }
    append_jsonl(queue_path, record)
    return record


def render_research_request_ack(record: dict[str, Any]) -> str:
    return (
        f"🧭 집현전-여행자: 심층 출처 리서치 요청을 등록했습니다. `{record['request_id']}` "
        f"주제: {record['topic']} / 최소 검토 출처: {record['min_sources_to_review']}개. "
        "다수 출처를 비교·탈락 기록까지 남긴 뒤 적합 후보만 광부 seed/클로 리뷰로 넘깁니다."
    )
