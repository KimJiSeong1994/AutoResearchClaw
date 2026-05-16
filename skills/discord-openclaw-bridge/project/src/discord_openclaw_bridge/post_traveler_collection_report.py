"""Post the daily Traveler collection-gap report to the Traveler forum.

The report is intentionally deterministic: it compares sites discovered by
집현전-여행자 (source-candidates queue) against the current miner collection
surface (miner seeds, intake, review queue, and approved manual links).  It then
surfaces candidates where additional collection is still useful, formatted for
Discord as an operator-readable document.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .miner import read_jsonl, sanitize_url
from .seeds import DEFAULT_SEEDS_PATH
from .traveler import default_source_queue_path

logger = logging.getLogger(__name__)

FORUM_CHANNEL_TYPE = 15
DISCORD_MESSAGE_LIMIT = 2000
DISCORD_THREAD_TITLE_LIMIT = 90
DEFAULT_MINER_INTAKE_PATH = Path.home() / ".openclaw" / "workspace" / "intake" / "jiphyeonjeon-miner" / "links.jsonl"
DEFAULT_MINER_REVIEW_QUEUE_PATH = Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-claw" / "link-review-queue.jsonl"
DEFAULT_MINER_APPROVED_EXPORT_PATH = Path.home() / ".openclaw" / "workspace" / "manual_links" / "approved-manual-links.jsonl"
DEFAULT_REPORT_STATE_PATH = Path.home() / ".openclaw" / "workspace" / "state" / "traveler-collection-report-last-status.json"
AGENT_ID = "jiphyeonjeon-traveler"
AGENT_DISPLAY_NAME = "집현전-여행자"


class TravelerReportConfigError(RuntimeError):
    """Raised when required report configuration is missing or invalid."""


@dataclass(frozen=True)
class CollectionContext:
    seed_urls: set[str]
    seed_hosts: set[str]
    collected_urls: set[str]
    collected_hosts: set[str]


@dataclass(frozen=True)
class ReportItem:
    site: str
    url: str
    analysis: str
    differentiation: str
    additional_info: str
    action: str
    priority: str


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return [row for row in read_jsonl(path) if isinstance(row, dict)]
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read jsonl %s: %s", path, exc)
        return []


def _row_url(row: dict[str, Any]) -> str:
    for key in ("url", "seed_url", "source_url"):
        value = sanitize_url(row.get(key, ""))
        if value:
            return value
    return ""


def _load_seed_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("could not read seeds %s: %s", path, exc)
        return set()
    urls: set[str] = set()
    for item in payload.get("seeds", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        url = sanitize_url(item.get("url", ""))
        if url:
            urls.add(url)
    return urls


def _load_collection_context() -> CollectionContext:
    seed_path = Path(os.environ.get("JIPHYEONJEON_MINER_SEEDS_PATH", str(DEFAULT_SEEDS_PATH))).expanduser()
    intake_path = Path(os.environ.get("JIPHYEONJEON_MINER_INTAKE_PATH", str(DEFAULT_MINER_INTAKE_PATH))).expanduser()
    review_path = Path(os.environ.get("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", str(DEFAULT_MINER_REVIEW_QUEUE_PATH))).expanduser()
    approved_path = Path(os.environ.get("JIPHYEONJEON_MINER_APPROVED_EXPORT_PATH", str(DEFAULT_MINER_APPROVED_EXPORT_PATH))).expanduser()

    seed_urls = _load_seed_urls(seed_path)
    collected_urls: set[str] = set(seed_urls)
    for path in (intake_path, review_path, approved_path):
        for row in _read_jsonl_rows(path):
            url = _row_url(row)
            if url:
                collected_urls.add(url)

    return CollectionContext(
        seed_urls=seed_urls,
        seed_hosts={host for url in seed_urls if (host := _host(url))},
        collected_urls=collected_urls,
        collected_hosts={host for url in collected_urls if (host := _host(url))},
    )


def _is_test_candidate(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "").lower()
        for key in ("title", "topic_fit", "reliability_rationale", "recommended_next_action")
    )
    tags = " ".join(str(tag).lower() for tag in row.get("tags", []) if isinstance(row.get("tags"), list))
    return any(marker in f"{text} {tags}" for marker in ("live test", "safe to ignore", "completed_test", "rejected_test", "연결 검증", "표시 검증"))


def _candidate_rows(path: Path) -> list[dict[str, Any]]:
    rows = _read_jsonl_rows(path)
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        url = _row_url(row)
        if not url:
            continue
        if row.get("status") not in {"pending_source_review", "accepted", "pending", None}:
            continue
        if _is_test_candidate(row):
            continue
        deduped[url] = row
    return list(deduped.values())


def _additional_info_for(row: dict[str, Any]) -> str:
    source_type = str(row.get("source_type") or "other")
    mapping = {
        "rss": "새 글 URL, 발행 시각, 제목/요약을 주기적으로 확보할 수 있습니다.",
        "archive_page": "목록 페이지의 신규 항목과 과거 아카이브 누락분을 함께 보강할 수 있습니다.",
        "newsletter_landing": "발행 주기, 이슈별 링크, 구독형 기술 동향을 보강할 수 있습니다.",
        "article_hub": "허브의 신규 글·시리즈·태그별 기술 글을 추가 수집할 수 있습니다.",
        "research_lab_blog": "연구 발표, 논문 해설, 데이터셋/모델 릴리스 정보를 확보할 수 있습니다.",
        "engineering_blog": "운영 경험, 아키텍처, 성능/장애 분석 등 실무 기술 정보를 확보할 수 있습니다.",
        "conference_feed": "컨퍼런스 논문/세션/프로시딩 갱신을 확보할 수 있습니다.",
        "dataset_release_feed": "데이터셋·벤치마크 릴리스와 변경 이력을 확보할 수 있습니다.",
        "manual_watch": "자동화 전 운영자가 선별해야 하는 후보 링크를 확보할 수 있습니다.",
    }
    base = mapping.get(source_type, "반복 관찰 가능한 신규 링크와 메타데이터를 확보할 수 있습니다.")
    topic_fit = str(row.get("topic_fit") or "").strip()
    return f"{base} {topic_fit}".strip()


def _analysis_for(row: dict[str, Any]) -> str:
    parts = []
    if row.get("reliability_rationale"):
        parts.append(f"신뢰 근거: {row['reliability_rationale']}")
    if row.get("update_cadence_evidence"):
        parts.append(f"갱신 단서: {row['update_cadence_evidence']}")
    if row.get("access_constraints"):
        parts.append(f"접근 조건: {row['access_constraints']}")
    return " / ".join(str(p).strip() for p in parts if str(p).strip()) or "탐험 큐에 등록됐지만 분석 메모가 부족합니다. 우선 샘플 수집으로 품질을 확인하세요."


def _differentiate(url: str, context: CollectionContext) -> tuple[str, str]:
    host = _host(url)
    if url in context.seed_urls:
        return "보류", "이미 광부 seed에 같은 URL이 있습니다. 추가 링크 보고 대상이 아닙니다."
    if url in context.collected_urls:
        return "보류", "이미 현재 수집/검토 큐에 같은 URL이 있습니다. 중복 수집보다 품질 점검이 우선입니다."
    if host and host in context.seed_hosts:
        return "중간", "같은 사이트는 seed에 있으나 이 경로/피드는 아직 seed가 아닙니다. 기존 seed가 커버하지 못하는 섹션인지 확인이 필요합니다."
    if host and host in context.collected_hosts:
        return "중간", "같은 사이트의 개별 콘텐츠는 수집됐지만 반복 수집 가능한 사이트/피드 단위 seed는 아닙니다."
    return "높음", "현재 seed·intake·review·approved 목록에 같은 URL/사이트가 없어 신규 수집면을 넓힐 수 있습니다."


def build_report_items(rows: Iterable[dict[str, Any]], context: CollectionContext, *, limit: int = 8) -> list[ReportItem]:
    items: list[ReportItem] = []
    for row in rows:
        url = _row_url(row)
        if not url or _is_test_candidate(row):
            continue
        priority, differentiation = _differentiate(url, context)
        if priority == "보류":
            continue
        site = str(row.get("title") or _host(url) or url).strip()
        action = str(row.get("recommended_next_action") or "광부 seed 후보로 샘플 수집 후 클로 리뷰").strip()
        items.append(
            ReportItem(
                site=site,
                url=url,
                analysis=_analysis_for(row),
                differentiation=differentiation,
                additional_info=_additional_info_for(row),
                action=action,
                priority=priority,
            )
        )
    order = {"높음": 0, "중간": 1, "낮음": 2}
    items.sort(key=lambda item: (order.get(item.priority, 9), item.site.lower()))
    return items[:limit]


def _today_kst(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")


def format_report_body(items: list[ReportItem], *, generated_at: datetime | None = None) -> str:
    date_label = _today_kst(generated_at)
    lines = [
        "# 🧭 집현전-여행자 추가 수집 링크 보고서",
        f"**보고일:** {date_label} 22:00 KST",
        f"**판정:** {'추가 수집 후보 있음' if items else '신규 추가 수집 후보 없음'}",
        "",
        "## 1. 3줄 요약",
    ]
    if items:
        high = sum(1 for item in items if item.priority == "높음")
        medium = sum(1 for item in items if item.priority == "중간")
        lines += [
            f"- 여행자 탐험 큐에서 추가 수집 후보 `{len(items)}`건을 선별했습니다.",
            f"- 우선순위는 높음 `{high}`건, 중간 `{medium}`건입니다.",
            "- 광부 seed/현재 수집 큐와 겹치지 않는 사이트·경로를 우선 보고합니다.",
        ]
    else:
        lines += [
            "- 여행자 탐험 큐 기준으로 신규 추가 수집 후보가 없습니다.",
            "- 기존 seed 또는 현재 수집/검토 큐와 중복되는 후보는 제외했습니다.",
            "- 다음 탐험 요청 또는 source-candidate 승인 후 다시 보고합니다.",
        ]

    lines += ["", "## 2. 추가 수집 필요 링크"]
    if not items:
        lines.append("- 없음")
    for idx, item in enumerate(items, 1):
        lines += [
            f"### {idx}. {item.site}",
            f"- **사이트:** {item.url}",
            f"- **우선순위:** {item.priority}",
            f"- **탐색/분석 결과:** {item.analysis}",
            f"- **현재 수집 내용과의 차별점:** {item.differentiation}",
            f"- **추가로 얻을 수 있는 정보:** {item.additional_info}",
            f"- **권장 액션:** {item.action}",
        ]

    lines += [
        "",
        "## 3. 운영 메모",
        "- 중복 방지를 위해 광부 seed, intake, 클로 review queue, approved export를 비교했습니다.",
        "- 보고 후보는 자동 승인 대상이 아니며, 클로 리뷰 후 seed 반영해야 합니다.",
        f"- _Reported by {AGENT_DISPLAY_NAME} (`{AGENT_ID}`)._",
    ]
    body = "\n".join(lines)
    if len(body) > DISCORD_MESSAGE_LIMIT:
        body = body[: DISCORD_MESSAGE_LIMIT - 3].rstrip() + "..."
    return body


def format_miner_collection_request(
    items: list[ReportItem],
    *,
    miner_client_id: str,
    traveler_thread_id: str | None = None,
    guild_id: str | None = None,
) -> str:
    mention = f"<@{miner_client_id}>"
    lines = [
        f"{mention} 🧭 집현전-여행자 추가 수집 요청",
        "",
        "Traveler가 오늘 심층 리서치/중복 비교 후 광부 수집 검토가 필요한 공개 출처를 선별했습니다.",
    ]
    if traveler_thread_id:
        if guild_id:
            lines.append(f"- Traveler 보고 스레드: <https://discord.com/channels/{guild_id}/{traveler_thread_id}>")
        else:
            lines.append(f"- Traveler 보고 스레드 ID: {traveler_thread_id}")
    lines += [
        "- 요청: 아래 출처를 광부 seed 후보로 샘플 수집하고, 클로 리뷰 큐로 넘겨주세요.",
        "- 승인 전에는 newsletter/manual approved 링크로 반영하지 마세요.",
        "",
        "## 추가 수집 요청",
    ]
    if not items:
        lines.append("- 신규 요청 없음")
    for idx, item in enumerate(items[:8], 1):
        lines += [
            f"{idx}. **{item.site}**",
            f"   - URL: {item.url}",
            f"   - 우선순위: {item.priority}",
            f"   - 차별점: {item.differentiation}",
            f"   - 기대 정보: {item.additional_info}",
        ]
    body = "\n".join(lines)
    if len(body) > DISCORD_MESSAGE_LIMIT:
        body = body[: DISCORD_MESSAGE_LIMIT - 3].rstrip() + "..."
    return body


async def _post_channel_message(
    client: httpx.AsyncClient,
    *,
    token: str,
    channel_id: str,
    body: str,
    mention_user_id: str | None = None,
) -> str:
    headers = {"Authorization": f"Bot {token}"}
    allowed_mentions: dict[str, Any] = {"parse": []}
    if mention_user_id:
        allowed_mentions["users"] = [mention_user_id]
    response = await client.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers=headers,
        json={"content": body, "allowed_mentions": allowed_mentions},
    )
    response.raise_for_status()
    return str(response.json().get("id") or "")


def _format_thread_title(items: list[ReportItem], *, generated_at: datetime | None = None) -> str:
    title = f"🧭 Traveler 추가 수집 보고 {_today_kst(generated_at)} — candidates={len(items)}"
    return title[:DISCORD_THREAD_TITLE_LIMIT]


async def _post_forum_thread(client: httpx.AsyncClient, *, token: str, channel_id: str, title: str, body: str) -> str:
    headers = {"Authorization": f"Bot {token}"}
    info = await client.get(f"https://discord.com/api/v10/channels/{channel_id}", headers=headers)
    info.raise_for_status()
    if int(info.json().get("type", 0)) != FORUM_CHANNEL_TYPE:
        raise TravelerReportConfigError(f"DISCORD_TRAVELER_CHANNEL_ID={channel_id} is not a forum channel")
    response = await client.post(
        f"https://discord.com/api/v10/channels/{channel_id}/threads",
        headers=headers,
        json={
            "name": title,
            "auto_archive_duration": 4320,
            "message": {"content": body, "allowed_mentions": {"parse": []}},
        },
    )
    response.raise_for_status()
    return str(response.json().get("id") or "")


def _write_status(
    path: Path,
    *,
    thread_id: str,
    title: str,
    item_count: int,
    miner_message_id: str | None = None,
) -> None:
    payload = {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "thread_id": thread_id,
        "title": title,
        "candidate_count": item_count,
        "miner_message_id": miner_message_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-post-traveler-collection-report",
        description="Post the daily Jiphyeonjeon Traveler additional-collection report.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Render the report to stdout without posting to Discord.")
    parser.add_argument("--skip-miner-request", action="store_true", help="Post only the Traveler forum report; skip the Miner bot-to-bot request.")
    return parser


async def run(*, dry_run: bool = False, skip_miner_request: bool = False) -> None:
    _load_dotenv(Path.cwd() / ".env")
    token = os.environ.get("DISCORD_TRAVELER_BOT_TOKEN", "").strip()
    channel_id = os.environ.get("DISCORD_TRAVELER_CHANNEL_ID", "").strip()

    source_queue = Path(os.environ.get("JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH", str(default_source_queue_path()))).expanduser()
    limit = int(os.environ.get("JIPHYEONJEON_TRAVELER_REPORT_MAX_ITEMS", "8"))
    status_path = Path(os.environ.get("JIPHYEONJEON_TRAVELER_REPORT_STATUS_PATH", str(DEFAULT_REPORT_STATE_PATH))).expanduser()
    miner_channel_id = os.environ.get("DISCORD_MINER_CHANNEL_ID", "").strip()
    miner_client_id = os.environ.get("DISCORD_MINER_CLIENT_ID", "").strip()
    guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()

    context = _load_collection_context()
    items = build_report_items(_candidate_rows(source_queue), context, limit=limit)
    title = _format_thread_title(items)
    body = format_report_body(items)

    miner_body = ""
    if not skip_miner_request and miner_client_id:
        miner_body = format_miner_collection_request(items, miner_client_id=miner_client_id)

    if dry_run:
        print(title)
        print()
        print(body)
        if miner_body:
            print("\n--- Miner bot-to-bot request ---\n")
            print(miner_body)
        return

    if not token:
        raise TravelerReportConfigError("missing DISCORD_TRAVELER_BOT_TOKEN")
    if not channel_id:
        raise TravelerReportConfigError("missing DISCORD_TRAVELER_CHANNEL_ID")

    miner_message_id: str | None = None
    async with httpx.AsyncClient(timeout=30) as client:
        thread_id = await _post_forum_thread(client, token=token, channel_id=channel_id, title=title, body=body)
        if not skip_miner_request:
            if not miner_channel_id or not miner_client_id:
                logger.warning("skipping Miner request: DISCORD_MINER_CHANNEL_ID or DISCORD_MINER_CLIENT_ID is missing")
            else:
                miner_body = format_miner_collection_request(
                    items,
                    miner_client_id=miner_client_id,
                    traveler_thread_id=thread_id,
                    guild_id=guild_id or None,
                )
                miner_message_id = await _post_channel_message(
                    client,
                    token=token,
                    channel_id=miner_channel_id,
                    body=miner_body,
                    mention_user_id=miner_client_id,
                )

    _write_status(status_path, thread_id=thread_id, title=title, item_count=len(items), miner_message_id=miner_message_id)
    logger.info(
        "posted traveler collection report channel=%s thread=%s candidates=%d miner_message=%s",
        channel_id,
        thread_id,
        len(items),
        miner_message_id,
    )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)
    try:
        asyncio.run(run(dry_run=args.dry_run, skip_miner_request=args.skip_miner_request))
    except (TravelerReportConfigError, httpx.HTTPError, ValueError) as exc:
        print(f"traveler collection report error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
