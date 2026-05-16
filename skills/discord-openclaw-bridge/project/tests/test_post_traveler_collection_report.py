from __future__ import annotations

import json
from pathlib import Path

from discord_openclaw_bridge.post_traveler_collection_report import (
    CollectionContext,
    build_report_items,
    format_report_body,
)


def test_traveler_collection_report_surfaces_gap_fields() -> None:
    rows = [
        {
            "title": "Example Research Lab Blog",
            "url": "https://research.example.com/blog",
            "source_type": "research_lab_blog",
            "reliability_rationale": "Official lab publication channel.",
            "update_cadence_evidence": "Weekly posts observed.",
            "topic_fit": "AI systems and evaluation reports.",
            "access_constraints": "public_http",
            "recommended_next_action": "review_for_miner_seed",
            "status": "pending_source_review",
        }
    ]
    context = CollectionContext(
        seed_urls=set(),
        seed_hosts=set(),
        collected_urls={"https://already.example.com/post"},
        collected_hosts={"already.example.com"},
    )

    items = build_report_items(rows, context)
    body = format_report_body(items)

    assert len(items) == 1
    assert "# 🧭 집현전-여행자 추가 수집 링크 보고서" in body
    assert "**사이트:** https://research.example.com/blog" in body
    assert "**탐색/분석 결과:**" in body
    assert "**현재 수집 내용과의 차별점:**" in body
    assert "**추가로 얻을 수 있는 정보:**" in body


def test_traveler_collection_report_skips_exact_duplicates() -> None:
    url = "https://research.example.com/blog"
    rows = [{"title": "Duplicate", "url": url, "status": "pending_source_review"}]
    context = CollectionContext(
        seed_urls={url},
        seed_hosts={"research.example.com"},
        collected_urls={url},
        collected_hosts={"research.example.com"},
    )

    items = build_report_items(rows, context)
    body = format_report_body(items)

    assert items == []
    assert "신규 추가 수집 후보 없음" in body
