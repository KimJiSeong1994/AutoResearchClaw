from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_openclaw_bridge.briefing import render_briefing  # noqa: E402


class BriefingRenderTests(unittest.TestCase):
    def test_weekly_report_prefers_adjacent_raw_json(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            note = base / "research-trends.md"
            note.write_text(
                "---\nreport_type: weekly-soul-trends\nsoul_source: soul\n---\n"
                "# Weekly research trends\n\n"
                "## At a glance\n\nmarkdown fallback conclusion\n\n"
                "## Trend clusters\n\n### Markdown-only cluster\n",
                encoding="utf-8",
            )
            (base / "raw.json").write_text(
                json.dumps(
                    {
                        "run_at": "2026-05-04T00:00:00+00:00",
                        "soul_source": "soul",
                        "soul_fallback_used": False,
                        "report": {
                            "at_a_glance": "raw conclusion",
                            "clusters": [{"title": "Raw cluster"}],
                        },
                        "candidates": [
                            {
                                "title": "Raw Paper",
                                "url": "https://example.test/raw-paper",
                                "trend_query": "graph retrieval",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            briefing = render_briefing(note)

        self.assertEqual(briefing.title, "Weekly research trends — 2026-05-04")
        self.assertIn("+ raw.json", briefing.body)
        self.assertIn("SOUL 기반", briefing.body)
        self.assertIn("raw conclusion", briefing.body)
        self.assertIn("Raw cluster", briefing.body)
        self.assertIn("Raw Paper: https://example.test/raw-paper", briefing.body)
        self.assertNotIn("markdown fallback conclusion", briefing.body)
        self.assertNotIn("Markdown-only cluster", briefing.body)

    def test_weekly_raw_profile_fallback_keeps_basis_visible(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            note = base / "research-trends.md"
            note.write_text("# Weekly research trends\n", encoding="utf-8")
            (base / "raw.json").write_text(
                json.dumps(
                    {
                        "run_at": "2026-05-04T00:00:00+00:00",
                        "soul_source": "profile_narrative_fallback",
                        "soul_fallback_used": True,
                        "report": {
                            "coverage_caveat": "fallback evidence caveat",
                            "clusters": [{"title": "Fallback cluster"}],
                        },
                        "candidates": [{"title": "Arxiv Paper", "arxiv_id": "2604.00001"}],
                    }
                ),
                encoding="utf-8",
            )

            briefing = render_briefing(note)

        self.assertIn("Profile Fallback", briefing.body)
        self.assertIn("(SOUL fallback)", briefing.body)
        self.assertIn("source=profile_narrative_fallback", briefing.body)
        self.assertIn("fallback evidence caveat", briefing.body)
        self.assertIn("https://arxiv.org/abs/2604.00001", briefing.body)

    def test_invalid_adjacent_raw_json_preserves_markdown_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            note = base / "research-trends.md"
            note.write_text(
                "---\nreport_type: weekly-profile-trends\n"
                "soul_source: profile_narrative_fallback\n"
                "soul_fallback_used: true\n---\n"
                "# Weekly research trends\n\n"
                "## At a glance\n\nmarkdown conclusion\n\n"
                "## Trend clusters\n\n### Markdown fallback cluster\n\n"
                "## Reading queue\n\n1. **Markdown Paper** — graph retrieval\n",
                encoding="utf-8",
            )
            (base / "raw.json").write_text("not valid json", encoding="utf-8")

            briefing = render_briefing(note)

        self.assertIn("markdown conclusion", briefing.body)
        self.assertIn("Markdown fallback cluster", briefing.body)
        self.assertIn("Markdown Paper", briefing.body)
        self.assertIn("(SOUL fallback)", briefing.body)
        self.assertNotIn("+ raw.json", briefing.body)


if __name__ == "__main__":
    unittest.main()
