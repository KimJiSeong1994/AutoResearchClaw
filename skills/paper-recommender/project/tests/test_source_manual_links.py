from __future__ import annotations

import asyncio
import json
from pathlib import Path

from paper_recommender.sources import SourceLimits
from paper_recommender.sources.manual_links import ManualLinkSettings, ManualLinksAdapter


def test_manual_links_adapter_reads_user_provided_linkedin_metadata(tmp_path: Path) -> None:
    path = tmp_path / "links.jsonl"
    path.write_text(
        json.dumps(
            {
                "title": "Agentic AI governance in regulated teams",
                "url": "https://www.linkedin.com/posts/example-agentic-ai",
                "summary": "A practitioner note about agentic AI controls and audit trails.",
                "author": "Research Lead",
                "published_at": "2026-05-04",
                "tags": ["agentic ai", "governance"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))
    items = asyncio.run(adapter.fetch(["agentic ai"], SourceLimits(max_per_source=10)))

    assert len(items) == 1
    item = items[0]
    assert item.source == "linkedin_manual"
    assert item.venue == "LinkedIn user-provided link"
    assert item.year == 2026
    assert item.authors == ("Research Lead",)
    assert "manual-link" in item.tags


def test_manual_links_adapter_filters_topics_and_invalid_urls(tmp_path: Path) -> None:
    path = tmp_path / "links.jsonl"
    rows = [
        {"title": "Agentic AI update", "url": "https://example.test/a", "summary": "agents"},
        {"title": "Cooking", "url": "https://example.test/b", "summary": "food"},
        {"title": "Bad", "url": "file:///etc/passwd", "summary": "agentic ai"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))
    items = asyncio.run(adapter.fetch(["agentic ai"], SourceLimits(max_per_source=10)))

    assert [it.title for it in items] == ["Agentic AI update"]


def test_manual_links_adapter_rejects_symlink(tmp_path: Path) -> None:
    path = tmp_path / "links.jsonl"
    path.write_text('{"title":"Agentic AI","url":"https://example.test"}\n', encoding="utf-8")
    link = tmp_path / "link.jsonl"
    link.symlink_to(path)

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(link)]))
    items = asyncio.run(adapter.fetch(["agentic"], SourceLimits()))

    assert items == []


def test_manual_links_rejects_pending_miner_review_queue(tmp_path: Path) -> None:
    path = tmp_path / "pending.jsonl"
    path.write_text(
        json.dumps(
            {
                "title": "Pending Miner Link",
                "url": "https://example.com/pending",
                "source": "discord_miner",
                "status": "pending_claw_review",
                "review": {"required": True, "decision": "pending", "newsletter_reflection": "blocked_until_approved"},
                "tags": ["discord-link", "jiphyeonjeon-miner", "pending_claw_review"],
            }
        )
        + "\n"
        + json.dumps(
            {
                "title": "Approved Miner Link",
                "url": "https://example.com/approved",
                "source": "discord_miner",
                "review": {"decision": "approved", "source_decision": "approve"},
                "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))

    items = asyncio.run(adapter.fetch([], SourceLimits(max_per_source=10)))

    assert [item.title for item in items] == ["Approved Miner Link"]


def test_manual_links_adapter_rejects_private_and_credentialed_urls(tmp_path: Path) -> None:
    path = tmp_path / "links.jsonl"
    rows = [
        {"title": "Localhost", "url": "http://localhost:8000/a", "summary": "agentic ai"},
        {"title": "Loopback", "url": "http://127.0.0.1/a", "summary": "agentic ai"},
        {"title": "Private", "url": "https://10.0.0.1/a", "summary": "agentic ai"},
        {"title": "Creds", "url": "https://user:pass@example.com/a", "summary": "agentic ai"},
        {"title": "Public", "url": "https://example.com/a", "summary": "agentic ai"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))
    items = asyncio.run(adapter.fetch(["agentic ai"], SourceLimits(max_per_source=10)))

    assert [it.title for it in items] == ["Public"]


def test_manual_links_rejects_pending_traveler_source_candidate(tmp_path: Path) -> None:
    path = tmp_path / "traveler-pending.jsonl"
    rows = [
        {
            "title": "Pending Traveler Source",
            "url": "https://example.com/source",
            "source": "discord_traveler",
            "status": "pending_source_review",
            "review": {"required": True, "decision": "pending", "miner_seed_expansion": "blocked_until_reviewed"},
            "tags": ["source-discovery", "jiphyeonjeon-traveler", "pending_source_review"],
        },
        {
            "title": "Approved Traveler Source",
            "url": "https://example.com/approved",
            "source": "discord_traveler",
            "review": {"decision": "approved", "source_decision": "approve"},
            "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))

    items = asyncio.run(adapter.fetch([], SourceLimits(max_per_source=10)))

    assert [item.title for item in items] == ["Approved Traveler Source"]


def test_manual_links_adapter_preserves_video_media_metadata(tmp_path: Path) -> None:
    path = tmp_path / "approved-youtube.jsonl"
    path.write_text(
        json.dumps(
            {
                "title": "LLM agent benchmark lecture",
                "url": "https://www.youtube.com/watch?v=abc123XYZ09",
                "summary": "retrieval benchmark and agent evaluation",
                "source": "discord_miner",
                "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
                "review": {"decision": "approved", "source_decision": "approve"},
                "media": {
                    "type": "video",
                    "platform": "youtube",
                    "video_id": "abc123XYZ09",
                    "analysis_status": "metadata_ready",
                    "analysis_provenance": "metadata_only",
                    "raw_provider_payload": {"secret": "x"},
                },
                "transcript": "must not persist",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))
    items = asyncio.run(adapter.fetch(["agent"], SourceLimits(max_per_source=10)))

    assert len(items) == 1
    assert ("media.video_id", "abc123XYZ09") in items[0].metadata
    dumped = json.dumps(items[0].metadata, ensure_ascii=False)
    assert "raw_provider_payload" not in dumped
    assert "must not persist" not in dumped


def test_manual_links_adapter_flattens_sanitized_content_analysis_metadata(tmp_path: Path) -> None:
    path = tmp_path / "approved-youtube-content-analysis.jsonl"
    path.write_text(
        json.dumps(
            {
                "title": "LLM agent benchmark lecture",
                "url": "https://www.youtube.com/watch?v=abc123XYZ09",
                "summary": "retrieval benchmark and agent evaluation",
                "source": "discord_miner",
                "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
                "review": {"decision": "approved", "source_decision": "approve"},
                "media": {"type": "video", "platform": "youtube", "video_id": "abc123XYZ09"},
                "content_analysis": {
                    "analysis_status": "ready",
                    "evidence_tier": "gemini_youtube_uri_no_transcript",
                    "analysis_provenance": "gemini_youtube_uri_no_transcript",
                    "provider": "gemini",
                    "summary_lines": ["provider-derived audiovisual inference", "자막/transcript 근거 아님"],
                    "claims": [{"text": "retrieval benchmark overview", "basis": "provider_model"}],
                    "raw_caption": "must not persist",
                    "fallback_reason": "https://example.com/?credential=SECRET",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))
    items = asyncio.run(adapter.fetch(["agent"], SourceLimits(max_per_source=10)))

    assert len(items) == 1
    assert ("content_analysis.evidence_tier", "model_public_youtube_av_no_raw") in items[0].metadata
    assert ("content_analysis.analysis_provenance", "model_public_youtube_av_no_raw") in items[0].metadata
    dumped = json.dumps(items[0].metadata, ensure_ascii=False)
    assert "raw_caption" not in dumped
    assert "credential=SECRET" not in dumped
    assert "자막/transcript 근거 아님" in dumped


def test_manual_links_adapter_rebuilds_sensitive_media_canonical_url(tmp_path: Path) -> None:
    path = tmp_path / "approved-youtube-sensitive.jsonl"
    path.write_text(
        json.dumps(
            {
                "title": "Technical YouTube Agent Benchmark",
                "url": "https://www.youtube.com/watch?v=abc123XYZ09",
                "summary": "LLM agent benchmark and retrieval evaluation.",
                "source": "discord_miner",
                "tags": ["manual-link", "approved-by-jiphyeonjeon-claw"],
                "review": {"decision": "approved", "source_decision": "approve"},
                "media": {
                    "type": "video",
                    "platform": "youtube",
                    "video_id": "abc123XYZ09",
                    "canonical_url": "https://www.youtube.com/watch?v=abc123XYZ09&key=AIzaSyFake&auth=x&code=y&sig=z",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    adapter = ManualLinksAdapter(ManualLinkSettings(paths=[str(path)]))
    items = asyncio.run(adapter.fetch(["agent"], SourceLimits(max_per_source=5)))

    assert items
    metadata = dict(items[0].metadata)
    assert metadata["media.canonical_url"] == "https://www.youtube.com/watch?v=abc123XYZ09"
    dumped = json.dumps(metadata, ensure_ascii=False)
    assert "AIzaSyFake" not in dumped
    assert "auth=" not in dumped
    assert "code=" not in dumped
    assert "sig=" not in dumped
