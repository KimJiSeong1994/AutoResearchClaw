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
            if row.get("candidate_id") != candidate_id:
                continue
            if row.get("status") in {"rejected_test", "completed_test", "cancelled_test", "ignored_test"}:
                continue
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

def render_research_prompt(record: dict[str, Any]) -> str:
    return (
        "집현전-여행자 역할로 심층 출처 리서치를 수행해 주세요. "
        "다수의 고신뢰 공개 출처를 비교하고, 탈락 기준까지 포함해 운영자가 바로 볼 수 있는 한국어 문서형 리포트를 작성하세요. "
        "실시간 웹 접근이 불가능하면 추측으로 출처를 꾸미지 말고, 확인 가능한 범위와 추가 확인 필요 항목을 명시하세요. "
        "Discord Markdown에서 읽기 좋도록 짧은 문단, 불릿, 표를 사용하고 1800자 이내로 압축하세요.\n\n"
        f"요청 ID: {record['request_id']}\n"
        f"주제: {record['topic']}\n"
        f"범위: {record['scope']}\n"
        f"최소 검토 출처 수: {record['min_sources_to_review']}\n"
        f"후보 기록 경로: {record['candidate_queue_path']}\n"
        f"요청 메모: {record.get('requester_note') or '(없음)'}\n\n"
        "반드시 아래 문서 포맷을 그대로 사용하세요:\n"
        "# 🧭 집현전-여행자 심층 리서치 리포트\n"
        f"**요청 ID:** `{record['request_id']}`\n"
        f"**주제:** {record['topic']}\n"
        "**판정:** <광부 seed 후보화 가능 / 추가 확인 필요 / 보류 중 하나>\n\n"
        "## 1. 3줄 요약\n"
        "- ...\n- ...\n- ...\n\n"
        "## 2. 추천 출처 후보\n"
        "|출처|URL/검색 단서|신뢰 근거|갱신|수집|판정|\n"
        "|---|---|---|---|---|---|\n"
        "|...|...|...|...|...|...|\n\n"
        "## 3. 보류/탈락 기준\n"
        "- ...\n\n"
        "## 4. 광부·클로 다음 액션\n"
        "- [광부] ...\n- [클로] ...\n\n"
        "## 5. 검증 한계\n"
        "- ..."
    )


def render_research_pending_notice(record: dict[str, Any]) -> str:
    return (
        "# 🧭 집현전-여행자 리서치 접수 보고서\n"
        f"**요청 ID:** `{record['request_id']}`\n"
        "**상태:** `pending_deep_research`\n"
        f"**주제:** {record['topic']}\n"
        f"**범위:** {record['scope']}\n"
        f"**최소 검토 출처:** {record['min_sources_to_review']}개\n"
        f"**후보 기록 경로:** `{record['candidate_queue_path']}`\n\n"
        "## 처리 방식\n"
        "- 다수 출처를 비교한 뒤 신뢰 근거와 탈락 사유를 함께 남깁니다.\n"
        "- 적합 후보만 집현전-광부 seed 또는 집현전-클로 리뷰로 넘깁니다.\n"
        "- 심층 리서치 결과는 이 스레드에 문서형 리포트로 이어서 게시됩니다."
    )


def render_research_request_ack(record: dict[str, Any]) -> str:
    return (
        "# 🧭 집현전-여행자 접수 완료\n"
        f"**요청 ID:** `{record['request_id']}`\n"
        f"**주제:** {record['topic']}\n"
        f"**최소 검토 출처:** {record['min_sources_to_review']}개\n"
        "**처리 원칙:** 다수 출처 비교 → 탈락 기록 → 적합 후보만 광부 seed/클로 리뷰로 전달"
    )
