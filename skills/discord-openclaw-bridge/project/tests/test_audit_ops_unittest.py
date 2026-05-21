from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from discord_openclaw_bridge import audit_ops


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def issue_signals(digest: dict[str, Any]) -> set[str]:
    return {str(issue["signal"]) for issue in digest["issues"]}


class AuditOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 5, 20, 13, 0, tzinfo=timezone.utc)
        (self.root / "runtime").mkdir()
        (self.root / "runtime" / "jobs.yaml").write_text("kind: runtime-jobs\n", encoding="utf-8")
        self.queue = self.root / "queue.jsonl"
        self.decisions = self.root / "decisions.jsonl"
        self.candidates = self.root / "candidate-review.jsonl"
        self.card_audit = self.root / "card-news-publication-audit.jsonl"
        self.trust_dir = self.root / "trust"
        write_jsonl(self.queue, [])
        write_jsonl(self.decisions, [])
        write_jsonl(self.candidates, [])
        write_jsonl(self.card_audit, [])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def digest(self, **kwargs: Any) -> dict[str, Any]:
        base = dict(
            now=self.now,
            runtime_jobs_path=self.root / "runtime" / "jobs.yaml",
            review_queue_path=self.queue,
            decisions_path=self.decisions,
            candidate_path=self.candidates,
            card_news_audit_path=self.card_audit,
            trust_gate_report_dir=self.trust_dir,
        )
        base.update(kwargs)
        return audit_ops.build_audit_digest(**base)

    def test_issue_schema_and_deterministic_id(self) -> None:
        missing = self.root / "missing-runner.sh"
        digest1 = self.digest(scheduler_wrappers=[missing])
        digest2 = self.digest(scheduler_wrappers=[missing])
        issue = digest1["issues"][0]
        for key in (
            "schema_version",
            "issue_id",
            "team_id",
            "suite",
            "signal",
            "severity",
            "observed_at",
            "evidence_refs",
            "recommended_action",
            "no_mutation",
        ):
            self.assertIn(key, issue)
        self.assertTrue(issue["issue_id"].startswith("audit_"))
        self.assertTrue(issue["no_mutation"])
        self.assertEqual(issue["issue_id"], digest2["issues"][0]["issue_id"])

    def test_default_cli_writes_no_files(self) -> None:
        issue_queue = self.root / "review" / "jiphyeonjeon-audit" / "issues.jsonl"
        audit_log = self.root / "logs" / "jiphyeonjeon-audit" / "audit-log.jsonl"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = audit_ops.main([
                "--runtime-jobs-path", str(self.root / "runtime" / "jobs.yaml"),
                "--review-queue-path", str(self.queue),
                "--decisions-path", str(self.decisions),
                "--candidate-path", str(self.candidates),
                "--card-news-audit-path", str(self.card_audit),
                "--trust-gate-report-dir", str(self.trust_dir),
                "--scheduler-wrapper", str(self.root / "missing.sh"),
                "--issue-queue-path", str(issue_queue),
                "--audit-log-path", str(audit_log),
            ])
        self.assertEqual(0, rc)
        payload = json.loads(buf.getvalue())
        self.assertEqual("jiphyeonjeon-audit-team", payload["team_id"])
        self.assertFalse(issue_queue.exists())
        self.assertFalse(audit_log.exists())

    def test_issue_queue_dedup_and_audit_log_append_only(self) -> None:
        digest = self.digest(scheduler_wrappers=[self.root / "missing.sh"])
        issue_queue = self.root / "review" / "jiphyeonjeon-audit" / "issues.jsonl"
        audit_log = self.root / "logs" / "jiphyeonjeon-audit" / "audit-log.jsonl"
        self.assertEqual(1, audit_ops.write_issue_queue(issue_queue, digest))
        self.assertEqual(0, audit_ops.write_issue_queue(issue_queue, digest))
        self.assertEqual(1, len(issue_queue.read_text(encoding="utf-8").splitlines()))
        first_count = audit_ops.write_audit_log(audit_log, digest)
        second_count = audit_ops.write_audit_log(audit_log, digest)
        self.assertGreater(first_count, 0)
        self.assertEqual(first_count, second_count)
        rows = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(first_count + second_count, len(rows))
        self.assertTrue(all(row["redaction_applied"] and row["no_mutation"] for row in rows))

    def test_secret_redaction_in_stdout_and_jsonl(self) -> None:
        secret_path = self.root / "sk-1234567890abcdefSECRET.sh"
        digest = self.digest(scheduler_wrappers=[secret_path])
        rendered = json.dumps(digest, ensure_ascii=False)
        self.assertNotIn("sk-1234567890abcdefSECRET", rendered)
        issue_queue = self.root / "review" / "jiphyeonjeon-audit" / "issues.jsonl"
        audit_log = self.root / "logs" / "jiphyeonjeon-audit" / "audit-log.jsonl"
        audit_ops.write_issue_queue(issue_queue, digest)
        audit_ops.write_audit_log(audit_log, digest)
        combined = issue_queue.read_text(encoding="utf-8") + audit_log.read_text(encoding="utf-8")
        self.assertNotIn("sk-1234567890abcdefSECRET", combined)
        self.assertIn("[REDACTED]", combined)

    def test_snapshot_freshness_signals(self) -> None:
        missing = self.root / "missing-snapshot.json"
        digest = self.digest(liveness_snapshot_path=missing)
        self.assertIn("snapshot_missing", issue_signals(digest))
        stale = self.root / "stale.json"
        stale.write_text(json.dumps({"snapshot_observed_at": "2026-05-19T00:00:00Z", "max_snapshot_age_seconds": 60}), encoding="utf-8")
        digest = self.digest(liveness_snapshot_path=stale)
        self.assertIn("snapshot_stale", issue_signals(digest))
        bad = self.root / "bad.json"
        bad.write_text(json.dumps({"snapshot_observed_at": "not-time"}), encoding="utf-8")
        digest = self.digest(liveness_snapshot_path=bad)
        self.assertIn("snapshot_unparseable", issue_signals(digest))
        fresh = self.root / "fresh.json"
        fresh.write_text(json.dumps({"snapshot_observed_at": "2026-05-20T12:59:00Z", "max_snapshot_age_seconds": 3600, "status": "ok"}), encoding="utf-8")
        digest = self.digest(liveness_snapshot_path=fresh)
        self.assertNotIn("snapshot_stale", issue_signals(digest))

    def test_scheduler_and_backlog_sla(self) -> None:
        write_jsonl(self.queue, [
            {"intake_id": "a", "created_at": "2026-05-01T00:00:00Z"},
            {"intake_id": "b", "created_at": "2026-05-20T00:00:00Z"},
        ])
        write_jsonl(self.decisions, [{"intake_id": "b", "decision": "approve", "reviewed_at": "2026-05-20T01:00:00Z"}])
        digest = self.digest(scheduler_wrappers=[self.root / "absent.sh"], max_pending_count=0, max_pending_age_days=1)
        signals = issue_signals(digest)
        self.assertIn("scheduler.runner_missing", signals)
        self.assertIn("pending_count_high", signals)
        self.assertIn("pending_age_high", signals)

    def test_card_audit_preferred_over_matching_summary_for_repeated_blocks(self) -> None:
        write_jsonl(self.card_audit, [
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-20T01:00:00Z", "artifact_hash": "same"},
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-19T01:00:00Z", "artifact_hash": "same"},
        ])
        self.trust_dir.mkdir()
        (self.trust_dir / "matching-summary.json").write_text(json.dumps({
            "decision": "block",
            "surface": "card-news",
            "reason_codes": ["weak_evidence"],
            "generated_at": "2026-05-18T01:00:00Z",
            "artifact_hash": "same",
        }), encoding="utf-8")
        digest = self.digest()
        self.assertNotIn("trust_gate_repeated_block", issue_signals(digest))

    def test_trust_gate_repeated_blocks_outside_window_do_not_escalate(self) -> None:
        write_jsonl(self.card_audit, [
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-20T01:00:00Z", "artifact_hash": "same"},
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-01T01:00:00Z", "artifact_hash": "same"},
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-04-30T01:00:00Z", "artifact_hash": "same"},
        ])
        digest = self.digest()
        self.assertNotIn("trust_gate_repeated_block", issue_signals(digest))

    def test_trust_gate_repeated_incident_window(self) -> None:
        rows = [
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-20T01:00:00Z", "artifact_hash": "same"},
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-19T01:00:00Z", "artifact_hash": "same"},
            {"decision": "blocked", "surface": "card-news", "reason_codes": ["weak_evidence"], "generated_at": "2026-05-18T01:00:00Z", "artifact_hash": "same"},
        ]
        write_jsonl(self.card_audit, rows)
        digest = self.digest()
        signals = issue_signals(digest)
        self.assertIn("trust_gate_block", signals)
        self.assertIn("trust_gate_repeated_block", signals)

    def test_provenance_schema_matrix(self) -> None:
        candidate = self.candidates
        write_jsonl(candidate, [{"candidate_id": "c1", "candidate_status": "needs_editorial_review", "url": "https://example.com", "publish_ready": True}])
        wiki = self.root / "page.md"
        wiki.write_text("---\ntitle: generated\n---\n", encoding="utf-8")
        digest = self.digest(candidate_path=candidate, wiki_pages=[wiki])
        signals = issue_signals(digest)
        self.assertIn("candidate_publish_ready_forbidden", signals)
        self.assertIn("wiki_generated_marker_missing", signals)

    def test_api_rate_budget_degraded_and_failure(self) -> None:
        degraded = self.root / "youtube.json"
        degraded.write_text(json.dumps({"snapshot_observed_at": "2026-05-20T12:59:00Z", "provider": "youtube", "status_code": 429, "fallback_success": True}), encoding="utf-8")
        failed = self.root / "discord.json"
        failed.write_text(json.dumps({"snapshot_observed_at": "2026-05-20T12:59:00Z", "provider": "discord", "status_code": 429}), encoding="utf-8")
        digest = self.digest(provider_status_paths=[degraded, failed])
        signals = issue_signals(digest)
        self.assertIn("provider_rate_limited_degraded", signals)
        self.assertIn("provider_failure", signals)

    def test_rejects_writes_to_content_review_paths(self) -> None:
        digest = self.digest(scheduler_wrappers=[self.root / "missing.sh"])
        with self.assertRaises(ValueError):
            audit_ops.write_issue_queue(self.queue, digest)
        with self.assertRaises(ValueError):
            audit_ops.write_audit_log(self.card_audit, digest)

    def test_missing_required_artifacts_are_not_ok(self) -> None:
        missing = self.root / "missing-queue.jsonl"
        digest = self.digest(review_queue_path=missing)
        signals = issue_signals(digest)
        self.assertIn("missing_artifact", signals)
        self.assertEqual("warning", digest["health_status"])

    def test_invalid_wrapper_and_stale_scheduler_inner_timestamp(self) -> None:
        wrapper = self.root / "bad.sh"
        wrapper.write_text("if then\n", encoding="utf-8")
        snapshot = self.root / "cron.json"
        snapshot.write_text(json.dumps({
            "snapshot_observed_at": "2026-05-20T12:59:00Z",
            "max_snapshot_age_seconds": 3600,
            "last_status_at": "2026-05-19T00:00:00Z",
            "max_inner_age_seconds": 60,
        }), encoding="utf-8")
        digest = self.digest(scheduler_wrappers=[wrapper], cron_snapshot_path=snapshot)
        signals = issue_signals(digest)
        self.assertIn("scheduler.runner_syntax_invalid", signals)
        self.assertIn("scheduler.last_status_at_stale", signals)

    def test_fresh_liveness_snapshot_with_stale_ready_at_warns(self) -> None:
        snap = self.root / "liveness.json"
        snap.write_text(json.dumps({
            "snapshot_observed_at": "2026-05-20T12:59:00Z",
            "max_snapshot_age_seconds": 3600,
            "ready_at": "2026-05-20T10:00:00Z",
            "max_liveness_age_seconds": 60,
        }), encoding="utf-8")
        digest = self.digest(liveness_snapshot_path=snap)
        self.assertIn("ready_at_stale", issue_signals(digest))

    def test_malformed_trust_summary_is_error(self) -> None:
        self.card_audit.unlink()
        self.trust_dir.mkdir()
        (self.trust_dir / "bad-summary.json").write_text("{bad", encoding="utf-8")
        digest = self.digest()
        self.assertIn("trust_summary_unparseable", issue_signals(digest))

    def test_malformed_trust_summary_is_error_even_with_card_audit_rows(self) -> None:
        write_jsonl(self.card_audit, [{"decision": "published", "surface": "card-news", "generated_at": "2026-05-20T01:00:00Z"}])
        self.trust_dir.mkdir()
        (self.trust_dir / "bad-summary.json").write_text("{bad", encoding="utf-8")
        digest = self.digest()
        self.assertIn("trust_summary_unparseable", issue_signals(digest))

    def test_provenance_requires_candidate_provenance_and_decision_reviewer(self) -> None:
        write_jsonl(self.decisions, [{"intake_id": "a", "decision": "approve", "created_at": "2026-05-20T00:00:00Z"}])
        write_jsonl(self.candidates, [{"candidate_id": "c", "candidate_status": "needs_editorial_review", "url": "https://example.com"}])
        digest = self.digest()
        signals = issue_signals(digest)
        self.assertIn("claw_decision_schema_missing", signals)
        self.assertIn("newsletter_candidate_schema_missing", signals)

    def test_api_repeated_rate_limit_and_no_report(self) -> None:
        paths = []
        for idx, day in enumerate((20, 19, 18)):
            p = self.root / f"provider-{idx}.json"
            p.write_text(json.dumps({
                "snapshot_observed_at": f"2026-05-{day:02d}T12:00:00Z",
                "provider": "youtube",
                "max_snapshot_age_seconds": 604800,
                "status_code": 429,
                "fallback_success": True,
                "report_success": False if idx == 0 else True,
            }), encoding="utf-8")
            paths.append(p)
        digest = self.digest(provider_status_paths=paths)
        signals = issue_signals(digest)
        self.assertIn("provider_repeated_rate_limit", signals)
        self.assertIn("provider_no_report", signals)

    def test_no_absolute_home_paths_in_output(self) -> None:
        secretish = Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-claw" / "link-review-queue.jsonl"
        digest = self.digest(review_queue_path=secretish)
        rendered = json.dumps(digest, ensure_ascii=False)
        self.assertNotIn(str(Path.home()), rendered)
        self.assertNotIn("/Users/", rendered)


if __name__ == "__main__":
    unittest.main()
