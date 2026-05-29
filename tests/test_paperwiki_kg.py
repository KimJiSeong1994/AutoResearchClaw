from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "paperwiki_kg.py"
FIXTURE = ROOT / "tests" / "fixtures" / "paperwiki_vault"


def run_cmd(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, text=True, capture_output=True)
    if check and cp.returncode != 0:
        raise AssertionError(f"command failed {cp.returncode}: {cp.args}\nSTDOUT={cp.stdout}\nSTDERR={cp.stderr}")
    return cp


class PaperWikiKGTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.vault = self.work / "vault"
        shutil.copytree(FIXTURE, self.vault)
        self.db = self.work / "paperwiki_kg.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def build(self, *extra: str) -> dict:
        cp = run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--json", *extra)
        return json.loads(cp.stdout)

    def test_build_schema_provenance_events_and_trust_defaults(self) -> None:
        out = self.build("--include-raw")
        self.assertTrue(out["ok"])
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual')")}
        for name in ["kg_sources", "kg_nodes", "kg_edges", "kg_chunks", "kg_events", "kg_provenance", "fts_chunks"]:
            self.assertIn(name, tables)
        generated = con.execute("SELECT trust_tier, excluded_reason FROM kg_sources WHERE path LIKE '%generated%'").fetchone()
        self.assertEqual(generated["trust_tier"], "generated-unreviewed")
        raw = con.execute("SELECT trust_tier FROM kg_sources WHERE path='raw/raw-source.md'").fetchone()
        self.assertEqual(raw["trust_tier"], "raw")
        self.assertGreater(con.execute("SELECT COUNT(*) FROM kg_provenance").fetchone()[0], 0)
        event = con.execute("SELECT * FROM kg_events LIMIT 1").fetchone()
        self.assertIn(event["event_type"], {"build", "source_added"})
        self.assertTrue(event["event_digest"])
        diag_codes = {r[0] for r in con.execute("SELECT code FROM kg_diagnostics")}
        self.assertIn("icloud_placeholder", diag_codes)
        self.assertIn("wikilink_ambiguous", diag_codes)
        self.assertIn("wikilink_unresolved", diag_codes)

    def test_query_returns_citations_and_excludes_untrusted_by_default(self) -> None:
        self.build("--include-raw")
        cp = run_cmd("query", "--db", str(self.db), "--query", "Graph RAG persistent KG", "--format", "json", "--strict", "--vault", str(self.vault))
        out = json.loads(cp.stdout)
        self.assertTrue(out["ok"])
        self.assertTrue(out["results"])
        paths = {r["path"] for r in out["results"]}
        self.assertIn("pages/Trusted.md", paths)
        self.assertNotIn("pages/generated/autoresearch-2026.md", paths)
        self.assertNotIn("raw/raw-source.md", paths)
        first = out["results"][0]
        for field in ["path", "chunk_id", "line_start", "line_end", "trust_tier", "source_hash", "edge_reasons", "citations", "warnings"]:
            self.assertIn(field, first)

    def test_status_strict_missing_and_stale_exit_codes(self) -> None:
        missing = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(missing.returncode, 5)
        self.build()
        fresh = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
        note = self.vault / "pages" / "Trusted.md"
        note.write_text(note.read_text(encoding="utf-8") + "\nNew drift.\n", encoding="utf-8")
        stale = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(stale.returncode, 2)
        self.assertEqual(json.loads(stale.stdout)["recommended_action"], "sync")

    def test_sync_idempotent_and_delete_tombstones(self) -> None:
        self.build()
        sync1 = json.loads(run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json").stdout)
        self.assertEqual(sync1["changed"], 0)
        self.assertEqual(sync1["tombstoned"], 0)
        target = self.vault / "pages" / "AliasTarget.md"
        target.unlink()
        sync2 = json.loads(run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json").stdout)
        self.assertEqual(sync2["tombstoned"], 1)
        con = sqlite3.connect(self.db)
        tomb = con.execute("SELECT tombstone FROM kg_sources WHERE path='pages/AliasTarget.md'").fetchone()[0]
        self.assertEqual(tomb, 1)

    def test_checkpoint_exports_manifest_and_jsonl(self) -> None:
        self.build()
        outdir = self.work / "checkpoint"
        out = json.loads(run_cmd("checkpoint", "--db", str(self.db), "--out", str(outdir), "--json").stdout)
        self.assertTrue(out["ok"])
        self.assertTrue((outdir / "manifest.json").exists())
        self.assertTrue((outdir / "kg_events.jsonl").exists())
        self.assertTrue((outdir / "paperwiki_kg.sqlite").exists())


class PaperWikiKGCriticRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.vault = self.work / "vault"
        shutil.copytree(FIXTURE, self.vault)
        self.db = self.work / "paperwiki_kg.sqlite"
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_query_expands_trusted_resolved_graph_neighbors(self) -> None:
        out = json.loads(run_cmd("query", "--db", str(self.db), "--query", "Graph RAG persistent KG", "--format", "json", "--strict", "--limit", "5").stdout)
        paths = [r["path"] for r in out["results"]]
        self.assertIn("pages/Trusted.md", paths)
        self.assertIn("pages/AliasTarget.md", paths)

    def test_include_raw_does_not_include_generated_quarantine(self) -> None:
        out = json.loads(run_cmd("query", "--db", str(self.db), "--query", "Graph RAG persistent KG", "--format", "json", "--include-raw", "--limit", "10").stdout)
        tiers = {r["path"]: r["trust_tier"] for r in out["results"]}
        self.assertIn("raw/raw-source.md", tiers)
        self.assertNotIn("pages/generated/autoresearch-2026.md", tiers)
        broad = json.loads(run_cmd("query", "--db", str(self.db), "--query", "Graph RAG persistent KG", "--format", "json", "--include-untrusted", "--limit", "10").stdout)
        self.assertIn("pages/generated/autoresearch-2026.md", {r["path"] for r in broad["results"]})

    def test_event_tail_tamper_invalidates_strict_status(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM kg_events WHERE event_id=(SELECT event_id FROM kg_events LIMIT 1)")
        con.commit()
        cp = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(cp.returncode, 3, cp.stdout)
        self.assertEqual(json.loads(cp.stdout)["error"], "event_tail_mismatch")

    def test_delete_sync_remains_fresh_and_new_target_reresolves_edges(self) -> None:
        target = self.vault / "pages" / "AliasTarget.md"
        target.unlink()
        sync = json.loads(run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json").stdout)
        self.assertGreaterEqual(sync["tombstoned"], 1)
        fresh = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(fresh.returncode, 0, fresh.stdout)

        missing = self.vault / "pages" / "Missing Note.md"
        missing.write_text("---\ntitle: Missing Note\n---\n# Missing Note\n\nNow resolvable.\n", encoding="utf-8")
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        con = sqlite3.connect(self.db)
        state = con.execute("SELECT resolution_state FROM kg_edges WHERE raw_target='Missing Note' AND tombstone=0 LIMIT 1").fetchone()[0]
        self.assertEqual(state, "resolved")

    def test_graphsearch_strict_missing_db_fails_closed(self) -> None:
        graphsearch = Path("/Users/jiseong/.codex/skills/graphsearch/scripts/graphsearch.py")
        cp = subprocess.run([
            sys.executable, str(graphsearch), "--vault", str(self.vault), "--db", str(self.work / "missing.sqlite"),
            "--query", "Graph RAG", "--format", "json", "--strict",
        ], text=True, capture_output=True)
        self.assertEqual(cp.returncode, 5, cp.stdout + cp.stderr)
        self.assertEqual(json.loads(cp.stdout)["error"], "persistent_kg_unavailable")

    def test_source_hash_mtime_sample_unchanged_by_build_query_sync(self) -> None:
        sample = sorted((self.vault / "pages").glob("*.md"))[:3]
        before = [(p, p.stat().st_mtime_ns, p.read_bytes()) for p in sample]
        run_cmd("query", "--db", str(self.db), "--query", "Graph RAG", "--format", "json", "--strict")
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        after = [(p, p.stat().st_mtime_ns, p.read_bytes()) for p in sample]
        self.assertEqual(before, after)
    def test_include_reports_is_narrow_opt_in(self) -> None:
        reports = self.vault / ".omx" / "reports"
        reports.mkdir(parents=True)
        (reports / "report.md").write_text("# Report\n\nGraph RAG persistent KG report evidence.\n", encoding="utf-8")
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-reports", "--json")
        default = json.loads(run_cmd("query", "--db", str(self.db), "--query", "report evidence", "--format", "json", "--limit", "10").stdout)
        self.assertNotIn(".omx/reports/report.md", {r["path"] for r in default["results"]})
        with_reports = json.loads(run_cmd("query", "--db", str(self.db), "--query", "report evidence", "--format", "json", "--include-reports", "--limit", "10").stdout)
        self.assertIn(".omx/reports/report.md", {r["path"] for r in with_reports["results"]})

    def test_sync_cleans_stale_provenance_for_modified_source(self) -> None:
        note = self.vault / "pages" / "Trusted.md"
        note.write_text(note.read_text(encoding="utf-8") + "\n# New Section\n\nreplacement chunk text\n", encoding="utf-8")
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        con = sqlite3.connect(self.db)
        orphan_count = con.execute("""
            SELECT COUNT(*) FROM kg_provenance p
            WHERE p.object_type='chunk' AND NOT EXISTS (SELECT 1 FROM kg_chunks c WHERE c.chunk_id=p.object_id)
        """).fetchone()[0]
        self.assertEqual(orphan_count, 0)

    def test_graphsearch_live_fallback_excludes_generated_pages(self) -> None:
        graphsearch = Path("/Users/jiseong/.codex/skills/graphsearch/scripts/graphsearch.py")
        cp = subprocess.run([
            sys.executable, str(graphsearch), "--vault", str(self.vault), "--query", "generated text",
            "--format", "json", "--no-persistent",
        ], text=True, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        paths = {r["path"] for r in json.loads(cp.stdout)["results"]}
        self.assertNotIn("pages/generated/autoresearch-2026.md", paths)

    def test_graph_and_event_digest_tamper_invalidates_strict_status(self) -> None:
        # Graph table tamper is detected by graph_digest validation.
        con = sqlite3.connect(self.db)
        con.execute("UPDATE kg_chunks SET text='tampered' WHERE chunk_id=(SELECT chunk_id FROM kg_chunks LIMIT 1)")
        con.commit()
        cp = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(cp.returncode, 3, cp.stdout)
        self.assertIn(json.loads(cp.stdout)["error"], {"graph_digest_mismatch", "fts_chunks_mismatch"})

        # Rebuild, then event row field tamper is detected by recomputing each event digest.
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE kg_events SET source_hash_after='tampered' WHERE event_id=(SELECT event_id FROM kg_events LIMIT 1)")
        con.commit()
        cp = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(cp.returncode, 3, cp.stdout)
        self.assertEqual(json.loads(cp.stdout)["error"], "event_tail_mismatch")

        # Rebuild, then assertion tamper is also covered by graph_digest.
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE kg_assertions SET assertion='tampered' WHERE assertion_id=(SELECT assertion_id FROM kg_assertions LIMIT 1)")
        con.commit()
        cp = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(cp.returncode, 3, cp.stdout)
        self.assertEqual(json.loads(cp.stdout)["error"], "graph_digest_mismatch")

    def test_fts_tamper_invalidates_strict_status_and_query(self) -> None:
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM fts_chunks")
        con.commit()
        status = run_cmd("status", "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(status.returncode, 3, status.stdout)
        self.assertEqual(json.loads(status.stdout)["error"], "fts_chunks_mismatch")
        query = run_cmd("query", "--db", str(self.db), "--query", "Graph RAG", "--format", "json", "--strict", check=False)
        self.assertEqual(query.returncode, 3, query.stdout)
        self.assertFalse(json.loads(query.stdout)["ok"])



if __name__ == "__main__":
    unittest.main()
