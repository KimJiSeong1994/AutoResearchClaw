#!/usr/bin/env python3
"""Convert the Apps Script Gmail relay payload into the canonical newsletter archive.

The Apps Script side owns Gmail authorization and public article fetching.  This
bridge owns EC2-side topic selection, raw archive persistence, and Discord-ready
Markdown rendering so the production path matches the Python newsletter ingest
schema without storing private email bodies.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path

import newsletter_ingest


_TRACKING_URL_HINTS = (
    "message.neo4j.com/",
    "medium.com/plans",
)

_JOB_URL_HINTS = (
    "linkedin.com/jobs",
    "linkedin.com/comm/jobs",
    "linkedin.com/jobs/view",
    "linkedin.com/job-collections",
)

_JOB_TEXT_HINTS = (
    "job alert",
    "job recommendation",
    "hiring",
    "채용",
    "채용공고",
    "구인",
    "지원하기",
)

_NON_TECH_URL_HINTS = (
    "linkedin.com/analytics",
    "linkedin.com/notifications",
    "linkedin.com/comm/notifications",
    "linkedin.com/comm/feed/update",
)

_NON_TECH_TEXT_HINTS = (
    "업데이트의 지난 주 노출수",
    "지난 주 노출수",
    "노출수",
    "프로필 조회",
    "게시물 조회",
    "회원님의 업데이트",
    "회원님의 게시물",
    "님 업데이트",
    "님 게시물",
    "impressions",
    "profile views",
    "post views",
    "people viewed your profile",
    "your update",
    "your post",
    "weekly stats",
    "analytics",
)

_NON_NEWSLETTER_SENDER_HINTS = (
    "no-reply@accounts.google.com",
    "security-noreply@",
)


def _clean(value: object) -> str:
    return newsletter_ingest._clean_text(str(value or ""))  # noqa: SLF001 - shared ingest sanitizer


def _is_tracking_url(url: str) -> bool:
    lower = url.lower()
    return any(hint in lower for hint in _TRACKING_URL_HINTS)


def _looks_like_link_dump(text: str) -> bool:
    lower = text.lower()
    return ("http://" in lower or "https://" in lower) and len(text) > 140


def is_job_related_item(raw: dict[str, object]) -> bool:
    url = _clean(raw.get("url")).lower()
    if any(hint in url for hint in _JOB_URL_HINTS):
        return True
    sender = _clean(raw.get("sender")).lower()
    title = _clean(raw.get("articleTitle") or raw.get("article_title") or raw.get("title")).lower()
    description = _clean(raw.get("articleDescription") or raw.get("article_description")).lower()
    snippet = _clean(raw.get("snippet")).lower()
    text = " ".join([sender, title, description, snippet])
    if "linkedin" in sender and any(hint in text for hint in _JOB_TEXT_HINTS):
        return True
    return False


def is_non_technical_notification_item(raw: dict[str, object]) -> bool:
    url = _clean(raw.get("url")).lower()
    sender = _clean(raw.get("sender")).lower()
    title = _clean(raw.get("articleTitle") or raw.get("article_title") or raw.get("title")).lower()
    description = _clean(raw.get("articleDescription") or raw.get("article_description")).lower()
    snippet = _clean(raw.get("snippet")).lower()
    text = " ".join([sender, title, description, snippet])
    is_linkedin = "linkedin" in sender or "linkedin.com" in url
    if is_linkedin and any(hint in url for hint in _NON_TECH_URL_HINTS):
        return True
    if is_linkedin and any(hint in text for hint in _NON_TECH_TEXT_HINTS):
        return True
    return False


def normalize_relay_item(raw: dict[str, object]) -> dict[str, object]:
    title = _clean(raw.get("title"))
    article_title = _clean(raw.get("articleTitle") or raw.get("article_title"))
    article_description = _clean(raw.get("articleDescription") or raw.get("article_description"))
    article_text = _clean(raw.get("articleText") or raw.get("article_text"))
    snippet = _clean(raw.get("snippet"))
    public_excerpt = article_description or article_text or snippet or title
    if _looks_like_link_dump(public_excerpt):
        public_excerpt = article_description or title
    public_excerpt = public_excerpt[:900]
    classification_text = " ".join(
        part
        for part in [
            title,
            article_title,
            article_description,
            article_text[:2500],
            _clean(raw.get("topic")),
            _clean(raw.get("url")),
        ]
        if part
    )
    raw_summary_lines = raw.get("summaryLines") or raw.get("summary_lines") or []
    if not isinstance(raw_summary_lines, list):
        raw_summary_lines = []

    item: dict[str, object] = {
        "title": title or article_title or "(untitled newsletter item)",
        "article_title": article_title,
        "article_description": article_description,
        "public_excerpt": public_excerpt,
        "url": _clean(raw.get("url")),
        "kind": _clean(raw.get("kind")) or "post",
        "sender": _clean(raw.get("sender")),
        "received_at": _clean(raw.get("receivedAt") or raw.get("received_at")),
        "snippet": snippet,
        "summary_lines": [
            _clean(line)
            for line in raw_summary_lines
            if _clean(line) and not _looks_like_link_dump(_clean(line))
        ][:3],
        # Used only in memory for topic selection; publish_items intentionally
        # omits it from raw archive output.
        "classification_text": classification_text,
    }
    classification = newsletter_ingest.classify_topic_detail(item)  # type: ignore[arg-type]
    item.update(
        {
            "primary_topic": classification.primary,
            "primary_topic_display": classification.primary_display,
            "secondary_topics": list(classification.secondary),
            "topic_confidence": classification.confidence,
            "topic_reasons": list(classification.reasons),
        }
    )
    return item


def load_relay_items(payload_path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if payload.get("error"):
        raise ValueError(f"apps script returned error: {payload['error']}")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("apps script relay payload has no items; deploy include_items support and pull with refresh=true")
    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        url = _clean(item.get("url"))
        sender = _clean(item.get("sender")).lower()
        if not url or any(hint in sender for hint in _NON_NEWSLETTER_SENDER_HINTS):
            continue
        if is_job_related_item(item):
            continue
        if is_non_technical_notification_item(item):
            continue
        if newsletter_ingest.is_private_utility_url(url) or _is_tracking_url(url):
            continue
        normalized_item = normalize_relay_item(item)
        key = (
            str(normalized_item.get("article_title") or normalized_item.get("title") or "").lower(),
            str(normalized_item.get("url") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)
    return payload, normalized


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, help="Apps Script relay JSON payload path")
    parser.add_argument("--wiki-root", required=True, help="PaperWiki/PaperWiki root")
    parser.add_argument("--date", default=_date.today().isoformat(), help="Run date folder/page date")
    parser.add_argument("--briefing-path", required=True, help="Markdown path for Discord-ready topic briefing")
    parser.add_argument("--max-items-per-topic", type=int, default=3)
    args = parser.parse_args(argv)

    payload_path = Path(args.payload).expanduser()
    wiki_root = Path(args.wiki_root).expanduser()
    briefing_path = Path(args.briefing_path).expanduser()
    try:
        payload, items = load_relay_items(payload_path)
        raw_path, page_path = newsletter_ingest.publish_items(
            wiki_root=wiki_root,
            run_date=args.date,
            source_path=payload_path,
            items=items,  # type: ignore[arg-type]
        )
        briefing_path.parent.mkdir(parents=True, exist_ok=True)
        source_name = f"Apps Script relay `{payload.get('query') or 'GmailApp search'}`"
        newsletter_ingest._atomic_write_text(  # noqa: SLF001 - shared atomic writer
            briefing_path,
            newsletter_ingest.render_topic_briefing(
                run_date=args.date,
                items=items,  # type: ignore[arg-type]
                source_name=source_name,
                max_items_per_topic=args.max_items_per_topic,
            ),
        )
    except Exception as exc:
        print(f"apps script relay ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {raw_path}")
    print(f"wrote {page_path}")
    print(f"wrote {briefing_path}")
    print(f"items: {len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
