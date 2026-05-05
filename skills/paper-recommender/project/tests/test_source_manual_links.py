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
