"""Autonomous scout request generator for 집현전-여행자.

Scout mode creates evidence-backed discovery requests when no operator request is
pending. It does not approve sources or mutate Miner seeds.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .miner import clean_text
from .traveler import TravelerResearchRequest, default_research_queue_path, record_research_request

DEFAULT_SCOUT_QUEUE_PATH = Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-traveler" / "scout-candidates.jsonl"
DEFAULT_SCOUT_STATUS_PATH = Path.home() / ".openclaw" / "workspace" / "state" / "traveler-scout-last-status.json"
DEFAULT_TOPICS = (
    {
        "id": "llm_agents",
        "query": "LLM agents research engineering",
        "scope": "high-trust recurring public research and engineering sources for LLM agents",
        "min_sources_to_review": 10,
        "max_candidates": 4,
        "priority": "high",
    },
    {
        "id": "rag_evaluation",
        "query": "RAG evaluation benchmark retrieval augmented generation",
        "scope": "public research and engineering sources for RAG evaluation and benchmarks",
        "min_sources_to_review": 10,
        "max_candidates": 4,
        "priority": "high",
    },
    {
        "id": "ai_infra",
        "query": "AI infrastructure inference serving systems research",
        "scope": "public technical sources for AI infrastructure, serving, inference, and systems research",
        "min_sources_to_review": 10,
        "max_candidates": 4,
        "priority": "medium",
    },
    {
        "id": "knowledge_graph",
        "query": "knowledge graph LLM retrieval agents research",
        "scope": "public research and engineering sources for knowledge graphs, retrieval, and agents",
        "min_sources_to_review": 10,
        "max_candidates": 3,
        "priority": "medium",
    },
)


@dataclass(frozen=True)
class ScoutTopic:
    topic_id: str
    query: str
    scope: str
    min_sources_to_review: int = 10
    max_candidates: int = 4
    priority: str = "medium"
    source: str = "configured"
    paperwiki_interest_slug: str = ""


def default_scout_queue_path() -> Path:
    return Path(os.environ.get("JIPHYEONJEON_TRAVELER_SCOUT_QUEUE_PATH", str(DEFAULT_SCOUT_QUEUE_PATH))).expanduser()


def default_scout_status_path() -> Path:
    return Path(os.environ.get("JIPHYEONJEON_TRAVELER_SCOUT_STATUS_PATH", str(DEFAULT_SCOUT_STATUS_PATH))).expanduser()



def _default_topics_path() -> Path | None:
    raw = os.environ.get("JIPHYEONJEON_TRAVELER_SCOUT_TOPICS_PATH", "").strip()
    return Path(raw).expanduser() if raw else None


def _topic_from_row(row: dict[str, Any]) -> ScoutTopic:
    topic_id = clean_text(row.get("id"), limit=120)
    query = clean_text(row.get("query"), limit=200)
    if not topic_id or not query:
        raise ValueError("scout topic requires non-empty id and query")
    scope = clean_text(row.get("scope") or "high-trust recurring public technical sources", limit=300)
    try:
        min_sources = int(row.get("min_sources_to_review") or 10)
    except (TypeError, ValueError):
        min_sources = 10
    try:
        max_candidates = int(row.get("max_candidates") or 4)
    except (TypeError, ValueError):
        max_candidates = 4
    priority = clean_text(row.get("priority") or "medium", limit=40).lower()
    if priority not in {"high", "medium", "low"}:
        priority = "medium"
    source = clean_text(row.get("source") or row.get("topic_source") or "configured", limit=80).lower()
    if source not in {"configured", "interest-note", "paperwiki-kg", "runtime"}:
        source = "configured"
    interest_slug = clean_text(row.get("paperwiki_interest_slug"), limit=120)
    return ScoutTopic(
        topic_id=topic_id,
        query=query,
        scope=scope,
        min_sources_to_review=max(10, min(min_sources, 80)),
        max_candidates=max(1, min(max_candidates, 20)),
        priority=priority,
        source=source,
        paperwiki_interest_slug=interest_slug,
    )


def load_scout_topics(path: Path | None = None) -> list[ScoutTopic]:
    topics_path = path or _default_topics_path()
    if topics_path is None:
        rows = list(DEFAULT_TOPICS)
    else:
        payload = json.loads(topics_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = payload.get("topics", [])
        else:
            rows = payload
    if not isinstance(rows, list):
        raise ValueError("scout topics config must contain a list")
    topics = [_topic_from_row(row) for row in rows if isinstance(row, dict)]
    if not topics:
        raise ValueError("at least one scout topic is required")
    seen: set[str] = set()
    deduped: list[ScoutTopic] = []
    for topic in topics:
        if topic.topic_id in seen:
            continue
        seen.add(topic.topic_id)
        deduped.append(topic)
    return deduped


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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


def _stale_pending_hours() -> float:
    raw = os.environ.get("JIPHYEONJEON_TRAVELER_SCOUT_STALE_PENDING_HOURS", "24").strip()
    try:
        hours = float(raw)
    except ValueError:
        return 24.0
    return max(0.0, min(hours, 24 * 14))


def _created_at_utc(row: dict[str, Any]) -> datetime | None:
    value = clean_text(row.get("created_at"), limit=80)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _pending_scout_topic_ids(path: Path, *, now: datetime | None = None) -> tuple[set[str], set[str]]:
    topic_ids: set[str] = set()
    stale_topic_ids: set[str] = set()
    stale_after_hours = _stale_pending_hours()
    now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for row in _read_jsonl_rows(path):
        if _is_test_research_request(row):
            continue
        if row.get("status") != "pending_deep_research":
            continue
        if row.get("discovery_mode") != "autonomous_scout":
            continue
        topic_id = clean_text(row.get("scout_topic_id"), limit=120)
        if not topic_id:
            continue
        created_at = _created_at_utc(row)
        if stale_after_hours:
            # A row with no parseable created_at cannot be aged, and treating it
            # as fresh blocked its topic permanently while reporting nothing —
            # the silent signature of the 2026-06-26 discovery outage. Rows lose
            # the field through legacy writes, manual queue recovery, or
            # truncated JSONL, so an unageable row is treated as stale. When the
            # window is disabled, missing timestamps keep blocking as before.
            age_hours = ((now_utc - created_at).total_seconds() / 3600) if created_at else None
            if age_hours is None or age_hours > stale_after_hours:
                stale_topic_ids.add(topic_id)
                continue
        topic_ids.add(topic_id)
    return topic_ids, stale_topic_ids


def _topics_status_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    mode = clean_text(os.environ.get("JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_MODE"), limit=80)
    path = clean_text(os.environ.get("JIPHYEONJEON_TRAVELER_TOPICS_SOURCE_PATH"), limit=500)
    fallback_reason = clean_text(os.environ.get("JIPHYEONJEON_TRAVELER_TOPICS_FALLBACK_REASON"), limit=160)
    trust_policy = clean_text(os.environ.get("JIPHYEONJEON_TRAVELER_TOPICS_TRUST_POLICY"), limit=120)
    generated_from_raw = os.environ.get("JIPHYEONJEON_TRAVELER_TOPICS_GENERATED_FROM", "").strip()
    if mode:
        metadata["topics_source_mode"] = mode
    if path:
        metadata["topics_source_path"] = path
    if fallback_reason:
        metadata["topics_fallback_reason"] = fallback_reason
    if trust_policy:
        metadata["topics_trust_policy"] = trust_policy
    if generated_from_raw:
        try:
            generated_from = json.loads(generated_from_raw)
        except json.JSONDecodeError:
            generated_from = {}
        if isinstance(generated_from, dict):
            safe_generated_from: dict[str, int] = {}
            for key in ("base_topics", "interests"):
                try:
                    safe_generated_from[key] = int(generated_from.get(key, 0))
                except (TypeError, ValueError):
                    safe_generated_from[key] = 0
            metadata["topics_generated_from"] = safe_generated_from
    return metadata


def create_scout_requests(
    *,
    topics: list[ScoutTopic],
    research_queue_path: Path | None = None,
    scout_queue_path: Path | None = None,
    status_path: Path | None = None,
    topic_filter: str | None = None,
    max_topics: int | None = None,
    dry_run: bool = False,
    skip_existing_pending: bool = True,
) -> dict[str, Any]:
    research_queue = (research_queue_path or default_research_queue_path()).expanduser()
    scout_queue = (scout_queue_path or default_scout_queue_path()).expanduser()
    selected = [topic for topic in topics if not topic_filter or topic.topic_id == topic_filter or topic.query == topic_filter]
    if max_topics is not None:
        selected = selected[: max(0, max_topics)]
    created: list[dict[str, Any]] = []
    skipped_existing: list[str] = []
    stale_pending: list[str] = []
    if skip_existing_pending:
        pending_topic_ids, stale_pending_topic_ids = _pending_scout_topic_ids(research_queue)
        stale_pending = sorted(stale_pending_topic_ids)
    else:
        pending_topic_ids = set()
    planned_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for topic in selected:
        if topic.topic_id in pending_topic_ids:
            skipped_existing.append(topic.topic_id)
            continue
        source_note = f" source={topic.source}" if topic.source != "configured" else ""
        request = TravelerResearchRequest(
            topic=topic.query,
            scope=topic.scope,
            min_sources_to_review=topic.min_sources_to_review,
            max_candidates=topic.max_candidates,
            requester_note=f"autonomous scout topic={topic.topic_id} priority={topic.priority}{source_note}",
            discovery_mode="autonomous_scout",
            scout_topic_id=topic.topic_id,
            scout_priority=topic.priority,
            topic_source=topic.source,
            paperwiki_interest_slug=topic.paperwiki_interest_slug,
        )
        if dry_run:
            record = {
                "status": "planned",
                "topic": request.topic,
                "scope": request.scope,
                "min_sources_to_review": request.min_sources_to_review,
                "max_candidates": request.max_candidates,
                "candidate_queue_path": str(scout_queue),
                "discovery_mode": request.discovery_mode,
                "scout_topic_id": request.scout_topic_id,
                "scout_priority": request.scout_priority,
                "topic_source": topic.source,
                "paperwiki_interest_slug": topic.paperwiki_interest_slug,
            }
        else:
            record = record_research_request(request, queue_path=research_queue, candidate_queue_path=scout_queue)
        created.append(record)
    status = {
        "run_at": planned_at,
        "dry_run": dry_run,
        "topics_seen": len(topics),
        "topics_selected": len(selected),
        "requests_created": 0 if dry_run else len(created),
        "requests_planned": len(created),
        "requests_skipped_existing": len(skipped_existing),
        "research_queue_path": str(research_queue),
        "scout_queue_path": str(scout_queue),
        "topics": [topic.topic_id for topic in selected],
        "topic_sources": {topic.topic_id: topic.source for topic in selected},
        "paperwiki_interest_slugs": {topic.topic_id: topic.paperwiki_interest_slug for topic in selected if topic.paperwiki_interest_slug},
        "skipped_existing_topics": skipped_existing,
        "stale_pending_topics": stale_pending,
        "stale_pending_hours": _stale_pending_hours(),
        "request_ids": [str(record.get("request_id")) for record in created if record.get("request_id")],
    }
    status.update(_topics_status_metadata())
    if status_path is not None and not dry_run:
        _write_status(status_path.expanduser(), status)
    return {"status": status, "requests": created}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-traveler-scout",
        description="Create autonomous Traveler scout research requests from configured topics.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned scout requests without appending queues.")
    parser.add_argument("--topics-path", type=Path, default=None, help="JSON file with {topics:[...]} or a list of topics.")
    parser.add_argument("--topic", default=None, help="Only run a matching scout topic id or query.")
    parser.add_argument("--max-topics", type=int, default=None, help="Maximum number of scout topics to enqueue.")
    parser.add_argument("--research-queue", type=Path, default=None, help="Override research request queue path.")
    parser.add_argument("--scout-queue", type=Path, default=None, help="Override autonomous scout candidate queue path.")
    parser.add_argument("--status-path", type=Path, default=None, help="Override scout status path.")
    parser.add_argument(
        "--allow-duplicate-pending",
        action="store_true",
        help="Append a scout request even when the same scout topic is already pending.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    _load_dotenv(Path.cwd() / ".env")
    args = _build_parser().parse_args(argv)
    try:
        topics = load_scout_topics(args.topics_path)
        result = create_scout_requests(
            topics=topics,
            research_queue_path=args.research_queue,
            scout_queue_path=args.scout_queue,
            status_path=args.status_path or default_scout_status_path(),
            topic_filter=args.topic,
            max_topics=args.max_topics,
            dry_run=args.dry_run,
            skip_existing_pending=not args.allow_duplicate_pending,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"traveler scout error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
