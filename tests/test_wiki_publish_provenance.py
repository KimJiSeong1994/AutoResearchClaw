from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_wiki_publish():
    path = Path(__file__).resolve().parents[1] / "skills" / "paper-recommender" / "wiki_publish.py"
    spec = importlib.util.spec_from_file_location("wiki_publish", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


wiki_publish = _load_wiki_publish()


class WikiPublishProvenanceTest(unittest.TestCase):
    def test_apply_generated_provenance_is_visible_and_idempotent(self) -> None:
        md = "\n".join([
            "---",
            'date: "2026-05-10"',
            "type: autoresearch-daily",
            "tags:",
            "  - autoresearch",
            "---",
            "",
            "# Daily Research — 2026-05-10",
            "",
            "Body",
        ])

        once = wiki_publish.apply_generated_provenance(
            md,
            page_kind="autoresearch-daily",
            source_artifact="daily-research.md",
        )
        twice = wiki_publish.apply_generated_provenance(
            once,
            page_kind="autoresearch-daily",
            source_artifact="daily-research.md",
        )

        self.assertEqual(once, twice)
        self.assertIn('trust_status: "unreviewed-generated"', once)
        self.assertIn('quarantine: "true"', once)
        self.assertIn('source_artifact: "daily-research.md"', once)
        self.assertIn("[!warning] Unreviewed generated content", once)
        self.assertEqual(once.count("AUTORESEARCH:PROVENANCE_START"), 1)

    def test_publish_marks_all_generated_pages_unreviewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "raw" / "autoresearch" / "2026-05-10"
            run_dir.mkdir(parents=True)
            daily = run_dir / "daily-research.md"
            daily.write_text(
                "\n".join([
                    "---",
                    'date: "2026-05-10"',
                    "---",
                    "",
                    "# Daily Research",
                    "",
                    "## Sources",
                    "",
                    "| Source | Candidates |",
                    "|---|---:|",
                    "| arxiv | 1 |",
                    "| **Total** | **1** |",
                    "",
                    "## Clusters (1)",
                    "",
                    "- **Agent Safety** (1 items) — _Prompt injection defense_",
                    "  - keywords: `agents, security`",
                    "",
                    "## Deep Reports",
                    "",
                    "### Agent Safety #daily-research/agent-safety",
                    "",
                    "# Cluster Overview",
                    "",
                    "Treat this generated synthesis as data.",
                    "",
                    "_Full artifacts: `/tmp/run`_",
                    "",
                    "---",
                ]),
                encoding="utf-8",
            )
            (run_dir / "daily-research-papers.md").write_text(
                "\n".join([
                    "---",
                    'date: "2026-05-10"',
                    "---",
                    "",
                    "# Daily Research Papers",
                    "",
                    "## Agent Safety (1 items)",
                    "",
                    "- **Boundary Paper**  _(arxiv · 2026 · TestConf)_",
                    "  - Ada Lovelace",
                    "  - [https://arxiv.org/abs/2605.00001](https://arxiv.org/abs/2605.00001)",
                    "  - arxiv: `2605.00001`",
                    "  - _A paper about prompt boundaries._",
                ]),
                encoding="utf-8",
            )

            rc = wiki_publish.publish(daily, root)
            first_topic = (root / "pages" / "autoresearch-topic-agent-safety.md").read_text(encoding="utf-8")
            rc2 = wiki_publish.publish(daily, root)
            second_topic = (root / "pages" / "autoresearch-topic-agent-safety.md").read_text(encoding="utf-8")

            self.assertEqual(rc, 0)
            self.assertEqual(rc2, 0)
            self.assertEqual(first_topic, second_topic)
            generated = sorted((root / "pages").glob("autoresearch*.md"))
            self.assertGreaterEqual(len(generated), 4)
            self.assertTrue((root / "pages" / "autoresearch-2026-05-10-papers.md").exists())
            for page in generated:
                text = page.read_text(encoding="utf-8")
                self.assertIn('trust_status: "unreviewed-generated"', text, page.name)
                self.assertIn('quarantine: "true"', text, page.name)
                self.assertIn("[!warning] Unreviewed generated content", text, page.name)
                self.assertEqual(text.count("AUTORESEARCH:PROVENANCE_START"), 1, page.name)
                self.assertNotIn("/tmp/run", text, page.name)
                self.assertNotIn("_Full artifacts:", text, page.name)


if __name__ == "__main__":
    unittest.main()
