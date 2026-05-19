#!/usr/bin/env python3
"""Publish approved 집현전-광부 manual-link rows into newsletter archive.

This is the explicit non-Apps-Script insertion bridge for approved Miner rows.
It reads only review-approved JSONL exported by the Discord bridge review path,
normalizes public fields, and delegates archive/page/briefing rendering to
``newsletter_ingest``.  It does not read Gmail bodies, transcripts, or raw
provider payloads.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date as _date
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import newsletter_ingest

_APPROVED_TAG = "approved-by-jiphyeonjeon-claw"
_FORBIDDEN_KEYS = {
    "raw_provider_payload",
    "raw_transcript",
    "caption_text",
    "raw_caption",
    "audio_bytes",
    "audio_path",
    "video_bytes",
    "private_body",
    "credential",
    "credentials",
    "access_token",
    "refresh_token",
}


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(str(key) in _FORBIDDEN_KEYS or _contains_forbidden_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    if isinstance(value, str):
        lower = value.lower()
        return any(marker in lower for marker in ("token=", "access_token=", "refresh_token=", "secret=", "credential="))
    return False


def _clean(value: object, *, limit: int = 700) -> str:
    return newsletter_ingest._clean_text(str(value or ""))[:limit]  # noqa: SLF001 - shared sanitizer


def _approved(row: dict[str, Any], *, include_source: str) -> bool:
    source = _clean(row.get("source"), limit=80).lower()
    if include_source and source != include_source.lower():
        return False
    review = row.get("review") if isinstance(row.get("review"), dict) else {}
    tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    clean_tags = {_clean(tag, limit=120).lower() for tag in tags}
    return (
        _clean(review.get("decision"), limit=40).lower() == "approved"
        and _clean(review.get("source_decision"), limit=40).lower() in {"approve", "approved"}
        and _APPROVED_TAG in clean_tags
    )


def _sanitize_media(value: object) -> dict[str, object]:
    return newsletter_ingest._sanitize_media(value)  # noqa: SLF001 - shared archive allowlist


def _sanitize_content_analysis(value: object) -> dict[str, object]:
    return newsletter_ingest._sanitize_content_analysis(value)  # noqa: SLF001 - shared archive allowlist


def _row_to_item(row: dict[str, Any], *, source_label: str) -> dict[str, object] | None:
    title = _clean(row.get("article_title") or row.get("title"), limit=180)
    url = newsletter_ingest.sanitize_public_url(_clean(row.get("url"), limit=1000))
    if not title or not url or newsletter_ingest.is_private_utility_url(url):
        return None
    media = _sanitize_media(row.get("media"))
    summary = _clean(row.get("summary") or row.get("abstract"), limit=700)
    summary_lines = row.get("summary_lines") if isinstance(row.get("summary_lines"), list) else []
    clean_summary_lines = [_clean(line, limit=240) for line in summary_lines if _clean(line, limit=240)]
    if not clean_summary_lines and summary:
        clean_summary_lines = [summary]
    item: dict[str, object] = {
        "title": title,
        "article_title": title,
        "article_description": summary,
        "public_excerpt": summary,
        "url": url,
        "kind": _clean(row.get("kind"), limit=80) or ("video" if media else newsletter_ingest.classify_url(url)),
        "sender": source_label,
        "received_at": _clean(row.get("published_at") or row.get("created_at"), limit=80),
        "summary_lines": clean_summary_lines[:3],
    }
    if media:
        item["media"] = media
    content_analysis = _sanitize_content_analysis(row.get("content_analysis"))
    if content_analysis:
        item["content_analysis"] = content_analysis
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
    if not newsletter_ingest.academic_technical_eligibility(item).eligible:
        return None
    if _contains_forbidden_key(item):
        raise ValueError("forbidden raw/private field leaked into normalized Miner archive item")
    return item


def load_items(path: Path, *, source_label: str, include_source: str, max_items: int) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    seen: set[str] = set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSONL: {exc}") from exc
        if not isinstance(row, dict) or not _approved(row, include_source=include_source):
            continue
        item = _row_to_item(row, source_label=source_label)
        if item is None:
            continue
        media = item.get("media") if isinstance(item.get("media"), dict) else {}
        key = str(media.get("video_id") or item.get("url") or item.get("title"))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
        if max_items and len(items) >= max_items:
            break
    return items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-links-path", required=True)
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--date", default=_date.today().isoformat())
    parser.add_argument("--briefing-path", required=True)
    parser.add_argument("--source-label", default="집현전-광부 승인 큐")
    parser.add_argument("--max-items", type=int, default=50)
    parser.add_argument("--include-source", default="discord_miner")
    args = parser.parse_args(argv)

    try:
        source_path = Path(args.manual_links_path).expanduser()
        items = load_items(
            source_path,
            source_label=args.source_label,
            include_source=args.include_source,
            max_items=args.max_items,
        )
        raw_path, page_path = newsletter_ingest.publish_items(
            wiki_root=Path(args.wiki_root).expanduser(),
            run_date=args.date,
            source_path=source_path,
            items=items,  # type: ignore[arg-type]
        )
        briefing_path = Path(args.briefing_path).expanduser()
        briefing_path.parent.mkdir(parents=True, exist_ok=True)
        newsletter_ingest._atomic_write_text(  # noqa: SLF001 - shared atomic writer
            briefing_path,
            newsletter_ingest.render_topic_briefing(
                run_date=args.date,
                items=items,  # type: ignore[arg-type]
                source_name=args.source_label,
            ),
        )
    except Exception as exc:
        print(f"miner approved archive ingest failed: {exc}", file=sys.stderr)
        return 1
    print(f"items: {len(items)}")
    print(f"wrote {raw_path}")
    print(f"wrote {page_path}")
    print(f"wrote {briefing_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
