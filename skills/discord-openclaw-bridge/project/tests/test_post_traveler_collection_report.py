from __future__ import annotations

import json
from pathlib import Path

from discord_openclaw_bridge.post_traveler_collection_report import (
    CollectionContext,
    ReportItem,
    build_miner_collection_request_payload,
    build_report_items,
    format_miner_collection_request,
    format_report_body,
    should_reuse_miner_request,
    url_hash,
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


def test_traveler_collection_report_skips_live_test_candidates() -> None:
    rows = [
        {
            "title": "arXiv cs.AI recent submissions",
            "url": "https://arxiv.org/list/cs.AI/recent",
            "status": "pending_source_review",
            "topic_fit": "요청 주제 `LIVE TEST - 집현전 여행자 연결 검증`의 후보",
        },
        {
            "title": "Real AI Source",
            "url": "https://real.example.com/feed",
            "status": "pending_source_review",
            "topic_fit": "AI research engineering sources",
        },
    ]
    context = CollectionContext(seed_urls=set(), seed_hosts=set(), collected_urls=set(), collected_hosts=set())

    items = build_report_items(rows, context)

    assert [item.site for item in items] == ["Real AI Source"]


def test_format_miner_collection_request_mentions_miner_first() -> None:
    body = format_miner_collection_request(
        [
            ReportItem(
                site="Anthropic Research",
                url="https://www.anthropic.com/research",
                analysis="신뢰 근거",
                differentiation="신규 수집면",
                additional_info="연구 발표",
                action="review_for_miner_seed",
                priority="높음",
            )
        ],
        miner_client_id="12345",
        traveler_thread_id="999",
    )

    assert body.startswith("<@12345>")
    assert "집현전-여행자 추가 수집 요청" in body
    assert "https://www.anthropic.com/research" in body
    assert "승인 전에는" in body


def test_url_hash_uses_sanitized_public_url() -> None:
    assert url_hash("https://example.com/research?utm_source=x&ok=1") == url_hash("https://example.com/research?ok=1")
    assert url_hash("http://127.0.0.1/private") == ""


def test_miner_collection_request_payload_tracks_only_included_items() -> None:
    items = [
        ReportItem(
            site=f"Site {idx}",
            url=f"https://example.com/{idx}",
            analysis="신뢰 근거",
            differentiation="차별점",
            additional_info="정보",
            action="review_for_miner_seed",
            priority="높음",
        )
        for idx in range(10)
    ]

    payload = build_miner_collection_request_payload(items, miner_client_id="12345")

    assert payload["request_item_count"] == 8
    assert len(payload["requested_url_hashes"]) == 8
    assert payload["request_truncated"] is True
    assert "https://example.com/8" not in payload["body"]


def test_should_reuse_miner_request_requires_same_payload() -> None:
    payload = build_miner_collection_request_payload(
        [
            ReportItem(
                site="Site",
                url="https://example.com/0",
                analysis="신뢰 근거",
                differentiation="차별점",
                additional_info="정보",
                action="review_for_miner_seed",
                priority="높음",
            )
        ],
        miner_client_id="12345",
    )
    existing = {
        "title": "title",
        "thread_id": "thread",
        "miner_request_state": "sent",
        "miner_request_body_hash": payload["body_hash"],
        "miner_request_url_hashes": payload["requested_url_hashes"],
        "miner_message_id": "message",
    }

    assert should_reuse_miner_request(existing, title="title", thread_id="thread", payload=payload)
    changed = dict(existing)
    changed["miner_request_url_hashes"] = ["different"]
    assert not should_reuse_miner_request(changed, title="title", thread_id="thread", payload=payload)
