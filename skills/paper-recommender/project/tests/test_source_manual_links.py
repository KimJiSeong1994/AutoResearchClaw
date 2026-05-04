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
