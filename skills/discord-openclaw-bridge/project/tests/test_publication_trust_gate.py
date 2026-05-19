from __future__ import annotations

import json
from pathlib import Path

import pytest

from discord_openclaw_bridge.publication_trust_gate import (
    PublicationTrustGateConfig,
    PublicationTrustGateError,
    run_publication_trust_gate,
)


def _config(tmp_path: Path, **kwargs) -> PublicationTrustGateConfig:
    values = {
        "report_dir": tmp_path / "reports",
        "min_evidence": 2,
        "min_domains": 1,
        "block_on_advisor_non_pass": True,
        "block_on_editor_duplicates": True,
    }
    values.update(kwargs)
    return PublicationTrustGateConfig(**values)


def test_publication_trust_gate_allows_evidence_backed_unique_archive(tmp_path: Path) -> None:
    artifact = tmp_path / "items.json"
    artifact.write_text(
        json.dumps(
            {
                "items": [
                    {"title": "A", "url": "https://example.com/a", "summary": "See https://example.com/a"},
                    {"title": "B", "url": "https://research.example.org/b", "summary": "See https://research.example.org/b"},
                ]
            }
        ),
        encoding="utf-8",
    )

    summary = run_publication_trust_gate(artifact, surface="test", config=_config(tmp_path))

    assert summary["decision"] == "allow"
    assert summary["editor"]["duplicate_group_count"] == 0
    assert summary["advisor"]["quality_status"] == "pass"
    assert Path(summary["editor_report_path"]).exists()
    assert Path(summary["advisor_report_path"]).exists()


def test_publication_trust_gate_blocks_duplicate_archive(tmp_path: Path) -> None:
    artifact = tmp_path / "items.json"
    artifact.write_text(
        json.dumps(
            {
                "items": [
                    {"title": "A", "url": "https://example.com/a"},
                    {"title": "A copy", "url": "https://example.com/a?utm_source=x"},
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PublicationTrustGateError, match="editor_duplicate_groups"):
        run_publication_trust_gate(artifact, surface="test", config=_config(tmp_path))


def test_publication_trust_gate_blocks_weak_markdown_evidence(tmp_path: Path) -> None:
    artifact = tmp_path / "briefing.md"
    artifact.write_text("# Briefing\n\nThis revolutionary item has no public links.", encoding="utf-8")

    with pytest.raises(PublicationTrustGateError, match="advisor_fail"):
        run_publication_trust_gate(artifact, surface="newsletter", config=_config(tmp_path))
