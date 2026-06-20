from __future__ import annotations

import importlib.util
import json
import os
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



def write_note(vault: Path, rel_path: str, frontmatter: dict, body: str) -> Path:
    lines = ["---"]
    for key, value in frontmatter.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                inner = ", ".join(str(v) for v in value)
                lines.append(f"{key}: [{inner}]")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    text = "\n".join(lines) + "\n" + body
    target = vault / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


class PaperWikiInterestQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.vault = self.work / "vault"
        shutil.copytree(FIXTURE, self.vault)
        self.db = self.work / "paperwiki_kg.sqlite"
        write_note(
            self.vault, "pages/interests/llm-agents.md",
            {"type": "interest", "interest_status": "active", "interest_weight": 0.8,
             "related_tags": ["kg"], "seed_keywords": ["agent", "planning"], "source": "user"},
            "# LLM Agents\n\nInterest in LLM agents and planning.\n\n## Anchors\n- [[Alias Target]]\n",
        )
        write_note(
            self.vault, "pages/interests/muted-topic.md",
            {"type": "interest", "interest_status": "muted", "interest_weight": 0.7,
             "related_tags": ["kg"], "seed_keywords": ["graph"], "source": "user"},
            "# Muted Topic\n\nA muted interest.\n\n## Anchors\n- [[Alias Target]]\n",
        )
        write_note(
            self.vault, "pages/typed-note.md",
            {"type": "paper", "tags": ["kg"]},
            "# Typed Note\n\nA non-interest note carrying a type frontmatter.\n",
        )
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_node_type_prefix_guard(self) -> None:
        con = sqlite3.connect(self.db)
        interest_type = con.execute(
            "SELECT node_type FROM kg_nodes WHERE path='pages/interests/llm-agents.md'"
        ).fetchone()[0]
        self.assertEqual(interest_type, "interest")
        typed_type = con.execute(
            "SELECT node_type FROM kg_nodes WHERE path='pages/typed-note.md'"
        ).fetchone()[0]
        self.assertEqual(typed_type, "note")

    def test_status_strict_clean_with_interests(self) -> None:
        built = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        synced = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(synced.returncode, 0, synced.stdout + synced.stderr)

    def test_default_query_unaffected_by_interests(self) -> None:
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG", "--format", "json", "--limit", "5",
        ).stdout)
        paths = {r["path"] for r in out["results"]}
        self.assertIn("pages/Trusted.md", paths)
        self.assertFalse(any(p.startswith("pages/interests/") for p in paths))
        self.assertNotIn("interest_score", out["results"][0])

    def test_interest_boost_promotes_anchor(self) -> None:
        without = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG", "--format", "json", "--limit", "5",
        ).stdout)
        with_interest = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG",
            "--use-interests", "--format", "json", "--limit", "5",
        ).stdout)
        with_paths = [r["path"] for r in with_interest["results"]]
        self.assertIn("pages/AliasTarget.md", with_paths)
        alias_result = next(r for r in with_interest["results"] if r["path"] == "pages/AliasTarget.md")
        self.assertIn("llm-agents", alias_result["matched_interests"])
        self.assertGreater(alias_result["interest_score"], 0)
        without_paths = [r["path"] for r in without["results"]]
        self.assertIn("pages/AliasTarget.md", without_paths)
        self.assertLessEqual(with_paths.index("pages/AliasTarget.md"), without_paths.index("pages/AliasTarget.md"))

    def test_muted_interest_excluded(self) -> None:
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG",
            "--use-interests", "--format", "json", "--limit", "10",
        ).stdout)
        for r in out["results"]:
            self.assertNotIn("muted-topic", r.get("matched_interests", []))

    def test_trust_no_leak_under_interests(self) -> None:
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG",
            "--use-interests", "--format", "json", "--limit", "10",
        ).stdout)
        paths = {r["path"] for r in out["results"]}
        self.assertFalse(any(p.startswith("pages/generated/") for p in paths))

    def test_bad_weight_does_not_crash(self) -> None:
        write_note(
            self.vault, "pages/interests/bad.md",
            {"type": "interest", "interest_status": "active", "interest_weight": "high",
             "related_tags": ["kg"], "seed_keywords": ["agent"], "source": "user"},
            "# Bad\n\nBad weight interest.\n\n## Anchors\n- [[Alias Target]]\n",
        )
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG",
            "--use-interests", "--format", "json", "--limit", "10",
        ).stdout)
        self.assertTrue(out["ok"])

    def test_interest_notes_excluded_from_results(self) -> None:
        # M2: an interest note whose body literally contains its own seed_keywords
        # would self-match under FTS, but must never appear as a result under
        # --use-interests.
        write_note(
            self.vault, "pages/interests/selfmatch.md",
            {"type": "interest", "interest_status": "active", "interest_weight": 0.9,
             "related_tags": ["kg"], "seed_keywords": ["selfmatchterm"], "source": "user"},
            "# Self Match\n\nThis interest body mentions selfmatchterm directly.\n\n## Anchors\n- [[Alias Target]]\n",
        )
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "selfmatchterm",
            "--use-interests", "--format", "json", "--limit", "10",
        ).stdout)
        self.assertTrue(out["ok"])
        for r in out["results"]:
            self.assertFalse(
                r["path"].startswith("pages/interests/"),
                f"interest note leaked into results: {r['path']}",
            )

    def test_late_resolving_anchor_resyncs_and_stays_fresh(self) -> None:
        # Sync-time regression: an interest anchoring an initially-unresolved
        # [[Missing Note]] becomes resolved after the target is created + synced,
        # and status --strict stays clean.
        write_note(
            self.vault, "pages/interests/late.md",
            {"type": "interest", "interest_status": "active", "interest_weight": 0.8,
             "related_tags": ["kg"], "seed_keywords": ["agent"], "source": "user"},
            "# Late\n\nLate-resolving anchor.\n\n## Anchors\n- [[Missing Note]]\n",
        )
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        con = sqlite3.connect(self.db)
        pre = con.execute(
            "SELECT resolution_state FROM kg_edges WHERE source_id="
            "(SELECT source_id FROM kg_sources WHERE path='pages/interests/late.md') "
            "AND raw_target='Missing Note' AND tombstone=0"
        ).fetchone()[0]
        self.assertEqual(pre, "unresolved")
        con.close()
        missing = self.vault / "pages" / "Missing Note.md"
        missing.write_text("---\ntitle: Missing Note\n---\n# Missing Note\n\nNow resolvable.\n", encoding="utf-8")
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        status = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
        con = sqlite3.connect(self.db)
        post = con.execute(
            "SELECT resolution_state FROM kg_edges WHERE source_id="
            "(SELECT source_id FROM kg_sources WHERE path='pages/interests/late.md') "
            "AND raw_target='Missing Note' AND tombstone=0"
        ).fetchone()[0]
        self.assertEqual(post, "resolved")

    def test_modify_then_delete_interest_note_stays_fresh(self) -> None:
        # Sync-time regression: modifying then deleting an interest note keeps
        # status --strict clean after each sync.
        note = self.vault / "pages" / "interests" / "llm-agents.md"
        note.write_text(note.read_text(encoding="utf-8") + "\nAppended interest text.\n", encoding="utf-8")
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        after_modify = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(after_modify.returncode, 0, after_modify.stdout + after_modify.stderr)
        note.unlink()
        run_cmd("sync", "--vault", str(self.vault), "--db", str(self.db), "--json")
        after_delete = run_cmd("status", "--vault", str(self.vault), "--db", str(self.db), "--json", "--strict", check=False)
        self.assertEqual(after_delete.returncode, 0, after_delete.stdout + after_delete.stderr)

    def test_direct_anchor_to_untrusted_does_not_leak(self) -> None:
        # Trust regression: an interest directly anchoring the generated-unreviewed
        # note must not pull it into results under default trust.
        write_note(
            self.vault, "pages/interests/untrusted-anchor.md",
            {"type": "interest", "interest_status": "active", "interest_weight": 0.9,
             "related_tags": ["kg"], "seed_keywords": ["agent"], "source": "user"},
            "# Untrusted Anchor\n\nAnchors a generated note.\n\n## Anchors\n- [[autoresearch-2026]]\n",
        )
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG",
            "--use-interests", "--format", "json", "--limit", "10",
        ).stdout)
        paths = {r["path"] for r in out["results"]}
        self.assertNotIn("pages/generated/autoresearch-2026.md", paths)

    def test_all_muted_interests_fall_through_to_default_path(self) -> None:
        # Empty/all-muted active set: --use-interests must fall through to the
        # default path (no interest_score on the first result).
        for slug in ["llm-agents"]:
            note = self.vault / "pages" / "interests" / f"{slug}.md"
            text = note.read_text(encoding="utf-8").replace(
                "interest_status: active", "interest_status: muted")
            note.write_text(text, encoding="utf-8")
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json")
        out = json.loads(run_cmd(
            "query", "--db", str(self.db), "--query", "Graph RAG persistent KG",
            "--use-interests", "--format", "json", "--limit", "5",
        ).stdout)
        self.assertTrue(out["ok"])
        self.assertTrue(out["results"])
        self.assertNotIn("interest_score", out["results"][0])


class PaperWikiBootstrapTest(unittest.TestCase):
    BOOTSTRAP = ROOT / "scripts" / "bootstrap-interest-notes.py"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.vault = self.work / "vault"
        self.vault.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_bootstrap(self, *extra: str) -> subprocess.CompletedProcess[str]:
        cp = subprocess.run(
            [sys.executable, str(self.BOOTSTRAP), "--vault", str(self.vault), *extra],
            cwd=ROOT, text=True, capture_output=True,
        )
        if cp.returncode != 0:
            raise AssertionError(f"bootstrap failed {cp.returncode}: {cp.args}\nSTDOUT={cp.stdout}\nSTDERR={cp.stderr}")
        return cp

    def test_bootstrap_creates_idempotent(self) -> None:
        self.run_bootstrap("--json")
        interests = self.vault / "pages" / "interests"
        for slug in ["llm-agents", "rag-evaluation", "ai-infra", "knowledge-graph"]:
            self.assertTrue((interests / f"{slug}.md").exists(), slug)
        before = {p.name: p.read_bytes() for p in interests.glob("*.md")}
        self.run_bootstrap("--json")
        after = {p.name: p.read_bytes() for p in interests.glob("*.md")}
        self.assertEqual(before, after)

    def test_bootstrap_preserves_user_notes(self) -> None:
        target = write_note(
            self.vault, "pages/interests/llm-agents.md",
            {"type": "interest", "source": "user"},
            "# My Curated LLM Agents\n\nUnique hand-written body.\n",
        )
        original = target.read_bytes()
        out = json.loads(self.run_bootstrap("--json").stdout)
        self.assertEqual(target.read_bytes(), original)
        self.assertIn("pages/interests/llm-agents.md", out["skipped"])

    def test_bootstrap_preserves_user_note_with_leading_blank_line(self) -> None:
        # C1: a user note whose frontmatter is preceded by a blank line must be
        # recognized as user-owned and preserved byte-for-byte.
        target = self.vault / "pages" / "interests" / "llm-agents.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "\n---\ntype: interest\nsource: user\n---\n# Curated\n\nLeading blank line body.\n",
            encoding="utf-8",
        )
        original = target.read_bytes()
        out = json.loads(self.run_bootstrap("--json").stdout)
        self.assertEqual(target.read_bytes(), original)
        self.assertIn("pages/interests/llm-agents.md", out["skipped"])

    def test_bootstrap_preserves_note_without_closing_fence(self) -> None:
        # C1: an unclosed/malformed frontmatter must not be overwritten (sentinel
        # "unknown" -> preserve).
        target = self.vault / "pages" / "interests" / "llm-agents.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "---\ntype: interest\nsource: user\n# Curated\n\nNo closing fence body.\n",
            encoding="utf-8",
        )
        original = target.read_bytes()
        out = json.loads(self.run_bootstrap("--json").stdout)
        self.assertEqual(target.read_bytes(), original)
        self.assertIn("pages/interests/llm-agents.md", out["skipped"])

    def test_bootstrap_path_traversal_topic_id_creates_no_outside_file(self) -> None:
        # M1: a topic id containing ../ must not create any file outside
        # pages/interests/ and must not crash the run.
        topics = self.work / "traveler-topics.json"
        topics.write_text(
            json.dumps({"topics": [{"id": "../../escape", "query": "x", "priority": "high"}]}),
            encoding="utf-8",
        )
        before = {p for p in self.work.rglob("*") if p.is_file()}
        cp = self.run_bootstrap("--json", "--traveler-topics", str(topics))
        self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
        after = {p for p in self.work.rglob("*") if p.is_file()}
        created = after - before
        interests_root = (self.vault / "pages" / "interests").resolve()
        for p in created:
            self.assertTrue(
                str(p.resolve()).startswith(str(interests_root) + os.sep),
                f"file escaped interests dir: {p}",
            )

    def test_bootstrap_missing_topics_returns_error_no_traceback(self) -> None:
        # M3: a missing topics file returns nonzero exit with a valid JSON error
        # object and no traceback.
        cp = subprocess.run(
            [sys.executable, str(self.BOOTSTRAP), "--vault", str(self.vault),
             "--json", "--traveler-topics", str(self.work / "nope.json")],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertNotEqual(cp.returncode, 0)
        self.assertNotIn("Traceback", cp.stderr)
        payload = json.loads(cp.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    def test_bootstrap_duplicate_slug_processes_first_only(self) -> None:
        # m3: two topics sanitizing to the same slug -> first wins, rest skipped.
        topics = self.work / "traveler-topics.json"
        topics.write_text(
            json.dumps({"topics": [
                {"id": "llm_agents", "query": "first", "priority": "high"},
                {"id": "llm.agents", "query": "second", "priority": "high"},
            ]}),
            encoding="utf-8",
        )
        out = json.loads(self.run_bootstrap("--json", "--traveler-topics", str(topics)).stdout)
        self.assertIn("pages/interests/llm-agents.md", out["created"])
        self.assertTrue(
            any("duplicate_slug" in s for s in out["skipped"]),
            out["skipped"],
        )
        body = (self.vault / "pages" / "interests" / "llm-agents.md").read_text(encoding="utf-8")
        self.assertIn("first", body)
        self.assertNotIn("second", body)


class PaperWikiRecommendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.vault = self.work / "vault"
        shutil.copytree(FIXTURE, self.vault)
        self.db = self.work / "paperwiki_kg.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add_interest(self) -> None:
        write_note(
            self.vault, "pages/interests/llm-agents.md",
            {"type": "interest", "interest_status": "active", "interest_weight": 0.8,
             "related_tags": ["kg"], "seed_keywords": ["agent", "planning"], "source": "user"},
            "# LLM Agents\n\nInterest in LLM agents and planning.\n\n## Anchors\n- [[Alias Target]]\n",
        )

    def build(self, *extra: str) -> None:
        run_cmd("build", "--vault", str(self.vault), "--db", str(self.db), "--include-raw", "--json", *extra)

    def recommend_json(self, *extra: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return run_cmd("recommend", "--db", str(self.db), "--format", "json", *extra, check=check)

    def test_recommend_returns_interest_anchored_results(self) -> None:
        self.add_interest()
        self.build()
        out = json.loads(self.recommend_json().stdout)
        self.assertTrue(out["ok"])
        self.assertEqual(out["command"], "recommend")
        self.assertFalse(out["cold_start"])
        self.assertTrue(out["results"])
        paths = {r["path"] for r in out["results"]}
        self.assertIn("pages/AliasTarget.md", paths)
        self.assertFalse(any(p.startswith("pages/interests/") for p in paths))
        for r in out["results"]:
            self.assertIn("components", r)
            for key in ["fit", "freshness", "trust_w", "novelty"]:
                self.assertIn(key, r["components"])
            self.assertIn("why", r)
            self.assertIn("matched_interests", r)
            self.assertIn("score", r)

    def test_recommend_deterministic_with_as_of(self) -> None:
        self.add_interest()
        self.build()
        first = json.loads(self.recommend_json("--as-of", "2026-06-20T00:00:00Z").stdout)
        second = json.loads(self.recommend_json("--as-of", "2026-06-20T00:00:00Z").stdout)
        self.assertEqual(first["results"], second["results"])
        self.assertEqual(first["as_of"], second["as_of"])

    def test_recommend_freshness_prefers_recent(self) -> None:
        self.add_interest()
        self.build()
        con = sqlite3.connect(self.db)
        con.execute(
            "UPDATE kg_events SET applied_at='2020-01-01T00:00:00Z' WHERE source_path='pages/AliasTarget.md'"
        )
        con.commit()
        con.close()
        out = json.loads(self.recommend_json("--as-of", "2026-06-20T00:00:00Z").stdout)
        by_path = {r["path"]: r for r in out["results"]}
        self.assertIn("pages/AliasTarget.md", by_path)
        self.assertIn("pages/Trusted.md", by_path)
        self.assertLess(
            by_path["pages/AliasTarget.md"]["components"]["freshness"],
            by_path["pages/Trusted.md"]["components"]["freshness"],
        )

    def test_recommend_trust_gate(self) -> None:
        self.add_interest()
        self.build()
        default = json.loads(self.recommend_json().stdout)
        self.assertFalse(any(r["path"].startswith("pages/generated/") for r in default["results"]))
        with_raw = json.loads(self.recommend_json("--include-raw").stdout)
        self.assertFalse(any(r["path"].startswith("pages/generated/") for r in with_raw["results"]))

    def test_recommend_cold_start_empty(self) -> None:
        self.build()
        cp = self.recommend_json()
        out = json.loads(cp.stdout)
        self.assertEqual(cp.returncode, 0)
        self.assertTrue(out["ok"])
        self.assertTrue(out["cold_start"])
        self.assertEqual(out["results"], [])

    def test_recommend_diversity_cap(self) -> None:
        write_note(
            self.vault, "pages/interests/multichunk.md",
            {"type": "interest", "interest_status": "active", "interest_weight": 0.9,
             "related_tags": ["kg"], "seed_keywords": ["zorptopic"], "source": "user"},
            "# Multichunk\n\nInterest in zorptopic.\n",
        )
        write_note(
            self.vault, "pages/Crowded.md",
            {"title": "Crowded", "tags": ["kg"]},
            "# Section One\n\nzorptopic appears here.\n\n# Section Two\n\nzorptopic appears again.\n"
            "\n# Section Three\n\nzorptopic once more.\n\n# Section Four\n\nzorptopic yet again.\n",
        )
        self.build()
        out = json.loads(self.recommend_json("--max-per-source", "2", "--limit", "10").stdout)
        from collections import Counter
        counts = Counter(r["path"] for r in out["results"])
        for path, count in counts.items():
            self.assertLessEqual(count, 2, f"{path} exceeded max-per-source: {count}")

    def test_recommend_missing_db_graceful(self) -> None:
        cp = self.recommend_json(check=False)
        out = json.loads(cp.stdout)
        self.assertEqual(cp.returncode, 5)
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"], [])

    def test_recommend_strict_stale(self) -> None:
        self.add_interest()
        self.build()
        note = self.vault / "pages" / "Trusted.md"
        note.write_text(note.read_text(encoding="utf-8") + "\nDrift line.\n", encoding="utf-8")
        cp = run_cmd("recommend", "--db", str(self.db), "--format", "json", "--strict",
                     "--vault", str(self.vault), check=False)
        self.assertNotEqual(cp.returncode, 0)
        self.assertFalse(json.loads(cp.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
