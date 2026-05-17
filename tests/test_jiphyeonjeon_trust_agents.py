from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = ROOT / "scripts" / "jiphyeonjeon_content_identity.py"
QUALITY_PATH = ROOT / "scripts" / "jiphyeonjeon_research_quality_gate.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


identity = load_module("jiphyeonjeon_content_identity", IDENTITY_PATH)
quality = load_module("jiphyeonjeon_research_quality_gate", QUALITY_PATH)


class JiphyeonjeonTrustAgentsTest(unittest.TestCase):
    def test_editor_canonicalizes_doi_and_url_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "miner.jsonl"
            second = Path(tmp) / "newsletter.json"
            first.write_text(
                json.dumps({"title": "Paper A", "url": "https://example.com/a?utm_source=x&keep=1"})
                + "\n"
                + json.dumps({"title": "Paper B", "doi": "10.1234/ABC.DEF"})
                + "\n",
                encoding="utf-8",
            )
            second.write_text(
                json.dumps(
                    {
                        "items": [
                            {"title": "Paper A copy", "url": "https://example.com/a?keep=1&utm_campaign=y"},
                            {"title": "Paper B copy", "source_url": "https://doi.org/10.1234/abc.def"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report = identity.build_report([first, second])
        self.assertEqual("jiphyeonjeon-editor", report["agent_id"])
        self.assertEqual("집현정-편집자", report["agent_name"])
        self.assertTrue(report["no_mutation"])
        self.assertTrue(report["advisory_only"])
        self.assertTrue(report["requires_human_promotion_review"])
        self.assertEqual("pending_future_phase", report["downstream_status"])
        duplicate_keys = {group["canonical_key"] for group in report["duplicate_groups"]}
        self.assertIn("url:https://example.com/a?keep=1", duplicate_keys)
        self.assertIn("doi:10.1234/abc.def", duplicate_keys)

    def test_editor_strips_sensitive_urls_from_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "items.jsonl"
            artifact.write_text(
                json.dumps({"title": "token", "url": "https://example.com/a?token=SECRET&keep=1"}) + "\n"
                + json.dumps({"title": "creds", "url": "https://user:pass@example.com/private"}) + "\n"
                + json.dumps({"title": "local", "url": "http://localhost:8000/private?key=SECRET"}) + "\n",
                encoding="utf-8",
            )
            report = identity.build_report([artifact])
        rendered = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("SECRET", rendered)
        self.assertNotIn("user:pass", rendered)
        self.assertNotIn("localhost", rendered)
        urls = [item["url"] for group in report["duplicate_groups"] for item in group["items"]]
        self.assertNotIn("https://example.com/a?token=SECRET&keep=1", urls)
        self.assertEqual("url:https://example.com/a?keep=1", identity.canonical_key({"url": "https://example.com/a?token=SECRET&keep=1"}))

    def test_editor_normalizes_title_fallback(self) -> None:
        first = identity.canonical_key({"title": "  Agentic   Search: 평가와 한계! "})
        second = identity.canonical_key({"title": "Agentic Search 평가와 한계"})
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("title:"))

    def test_advisor_passes_evidence_rich_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "report.md"
            artifact.write_text(
                "Claim with source https://arxiv.org/abs/2601.00001 and another "
                "https://openreview.net/forum?id=abc123.",
                encoding="utf-8",
            )
            report = quality.evaluate(artifact, min_evidence=2, min_domains=2)
        self.assertEqual("jiphyeonjeon-advisor", report["agent_id"])
        self.assertEqual("집현전-지도교수", report["agent_name"])
        self.assertEqual("pass", report["quality_status"])
        self.assertTrue(report["quality_gate_passed"])
        self.assertFalse(report["publication_blocked"])
        self.assertTrue(report["advisory_only"])
        self.assertTrue(report["requires_human_promotion_review"])
        self.assertEqual("pending_future_phase", report["downstream_status"])
        self.assertTrue(report["no_mutation"])

    def test_advisor_fails_artifact_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "draft.md"
            artifact.write_text("This revolutionary summary has no citations.", encoding="utf-8")
            report = quality.evaluate(artifact, min_evidence=1, min_domains=1)
        self.assertEqual("fail", report["quality_status"])
        self.assertFalse(report["quality_gate_passed"])
        self.assertTrue(report["publication_blocked"])
        self.assertIn("evidence_url_count_below_1", report["issues"])
        self.assertIn("possible_overclaim_language", report["issues"])

    def test_advisor_computes_row_evidence_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "items.json"
            artifact.write_text(
                json.dumps(
                    {
                        "items": [
                            {"title": "with evidence", "url": "https://example.org/a"},
                            {"title": "without evidence", "summary": "missing source"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            report = quality.evaluate(artifact, min_evidence=1, min_domains=1)
        self.assertEqual(50, report["evidence_coverage_pct"])
        self.assertEqual("needs_review", report["quality_status"])
        self.assertIn("row_evidence_coverage_below_80_pct", report["issues"])

    def test_editor_does_not_mutate_inputs_and_cli_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "items.jsonl"
            artifact.write_text(json.dumps({"title": "A", "url": "https://arxiv.org/abs/2601.00001"}) + "\n", encoding="utf-8")
            before = artifact.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(IDENTITY_PATH), str(artifact)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(before, artifact.read_bytes())
            payload = json.loads(proc.stdout)
        self.assertTrue(payload["no_mutation"])
        self.assertEqual("jiphyeonjeon-editor", payload["agent_id"])

    def test_editor_handles_arxiv_and_openreview_variants(self) -> None:
        self.assertEqual("arxiv:2601.00001", identity.canonical_key({"url": "https://arxiv.org/abs/2601.00001v2"}))
        self.assertEqual("openreview:abc123", identity.canonical_key({"url": "https://openreview.net/forum?noteId=x&id=abc123"}))

    def test_advisor_ignores_asset_and_discord_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "draft.md"
            artifact.write_text(
                "Image https://cdn.discordapp.com/a.png and asset https://example.org/chart.svg",
                encoding="utf-8",
            )
            report = quality.evaluate(artifact, min_evidence=1, min_domains=1)
        self.assertEqual(0, report["evidence_url_count"])
        self.assertEqual("fail", report["quality_status"])

    def test_advisor_cli_reports_malformed_json_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "bad.json"
            artifact.write_text("{bad", encoding="utf-8")
            before = artifact.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(QUALITY_PATH), str(artifact)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(0, proc.returncode)
            self.assertEqual(before, artifact.read_bytes())



if __name__ == "__main__":
    unittest.main()
