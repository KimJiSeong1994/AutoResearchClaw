from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from discord_openclaw_bridge.guard_ops import build_ops_digest, write_issue_queue


class GuardOpsDigestTests(unittest.TestCase):
    def test_missing_status_is_error_issue(self) -> None:
        digest = build_ops_digest(status=None, status_path="/tmp/missing.json")

        self.assertEqual(digest["health_status"], "error")
        self.assertEqual(digest["issues"][0]["signal"], "missing_status")
        self.assertTrue(digest["issues"][0]["issue_id"].startswith("guard_"))

    def test_healthy_cooldown_status_is_ok(self) -> None:
        now = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        status = {
            "run_at": now.isoformat().replace("+00:00", "Z"),
            "seeds_total": 2,
            "seeds_skipped_cooldown": 2,
            "seeds_with_errors": 0,
            "total_accepted": 0,
        }

        digest = build_ops_digest(status=status, status_path="/tmp/status.json", now=now)

        self.assertEqual(digest["health_status"], "ok")
        self.assertEqual(digest["summary"]["issue_count"], 0)

    def test_seed_error_and_zero_accepted_are_reported(self) -> None:
        now = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        status = {
            "run_at": now.isoformat().replace("+00:00", "Z"),
            "seeds_total": 1,
            "seeds_skipped_cooldown": 0,
            "seeds_with_errors": 1,
            "total_accepted": 0,
        }

        digest = build_ops_digest(status=status, status_path="/tmp/status.json", now=now)

        signals = {issue["signal"] for issue in digest["issues"]}
        self.assertEqual(digest["health_status"], "error")
        self.assertIn("seed_errors", signals)
        self.assertNotIn("zero_accepted", signals)

    def test_stale_and_pending_review_backlog_are_reported(self) -> None:
        now = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        old = now - timedelta(days=8)
        status = {
            "run_at": (now - timedelta(hours=30)).isoformat().replace("+00:00", "Z"),
            "seeds_total": 1,
            "seeds_skipped_cooldown": 0,
            "seeds_with_errors": 0,
            "total_accepted": 1,
        }
        queue_rows = [
            {"intake_id": "miner_a", "created_at": old.isoformat().replace("+00:00", "Z")},
            {"intake_id": "miner_b", "created_at": now.isoformat().replace("+00:00", "Z")},
        ]

        digest = build_ops_digest(
            status=status,
            status_path="/tmp/status.json",
            queue_rows=queue_rows,
            decisions_rows=[],
            now=now,
            max_pending_count=1,
        )

        signals = {issue["signal"] for issue in digest["issues"]}
        self.assertEqual(digest["health_status"], "warning")
        self.assertIn("stale_status", signals)
        self.assertIn("pending_count_high", signals)
        self.assertIn("pending_age_high", signals)

    def test_backlog_evidence_uses_configured_queue_path(self) -> None:
        now = datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc)
        status = {
            "run_at": now.isoformat().replace("+00:00", "Z"),
            "seeds_total": 1,
            "seeds_skipped_cooldown": 0,
            "seeds_with_errors": 0,
            "total_accepted": 1,
        }
        digest = build_ops_digest(
            status=status,
            status_path="/tmp/status.json",
            review_queue_path="/custom/review.jsonl",
            queue_rows=[{"intake_id": "miner_a", "created_at": now.isoformat()}],
            decisions_rows=[],
            now=now,
            max_pending_count=0,
        )

        self.assertEqual(digest["issues"][0]["evidence"], "/custom/review.jsonl")

    def test_guard_config_reports_bridge_fallback_without_secret_values(self) -> None:
        digest = build_ops_digest(
            status={
                "run_at": "2026-05-10T00:00:00Z",
                "seeds_total": 1,
                "seeds_skipped_cooldown": 1,
                "seeds_with_errors": 0,
                "total_accepted": 0,
            },
            status_path="/tmp/status.json",
            guard_config={
                "guard_token_set": False,
                "bridge_token_set": True,
                "ops_report_channel_set": False,
                "token_source": "bridge-fallback",
            },
            now=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        )

        signals = {issue["signal"] for issue in digest["issues"]}
        rendered = str(digest)
        self.assertEqual(digest["guard_config"]["token_source"], "bridge-fallback")
        self.assertIn("guard_token_missing", signals)
        self.assertIn("ops_report_channel_default", signals)
        self.assertNotIn("secret", rendered)

    def test_guard_config_reports_missing_all_bot_tokens_as_error(self) -> None:
        digest = build_ops_digest(
            status={
                "run_at": "2026-05-10T00:00:00Z",
                "seeds_total": 1,
                "seeds_skipped_cooldown": 1,
                "seeds_with_errors": 0,
                "total_accepted": 0,
            },
            status_path="/tmp/status.json",
            guard_config={
                "guard_token_set": False,
                "bridge_token_set": False,
                "ops_report_channel_set": True,
                "token_source": "missing",
            },
            now=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(digest["health_status"], "error")
        self.assertEqual(digest["issues"][0]["signal"], "bot_token_missing")

    def test_write_issue_queue_appends_new_issue_once(self) -> None:
        with TemporaryDirectory() as tmpdir:
            queue_path = Path(tmpdir) / "ops-issue-queue.jsonl"
            digest = build_ops_digest(status=None, status_path="/tmp/missing.json")

            first = write_issue_queue(queue_path, digest)
            second = write_issue_queue(queue_path, digest)

            self.assertEqual(first, 1)
            self.assertEqual(second, 0)
            rows = queue_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            self.assertIn('"status": "open"', rows[0])

    def test_traveler_to_miner_handoff_reports_unconfirmed_intake(self) -> None:
        digest = build_ops_digest(
            status={
                "run_at": "2026-05-10T00:00:00Z",
                "seeds_total": 1,
                "seeds_skipped_cooldown": 1,
                "seeds_with_errors": 0,
                "total_accepted": 0,
            },
            status_path="/tmp/status.json",
            traveler_report_status={
                "miner_message_id": "123",
                "miner_request_url_hashes": ["abc"],
            },
            traveler_status_path="/tmp/traveler.json",
            miner_intake_rows=[],
            enable_traveler_handoff=True,
            now=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(digest["handoffs"]["traveler_to_miner"]["status"], "unconfirmed")
        self.assertEqual(digest["handoffs"]["traveler_to_miner"]["missing_count"], 1)
        self.assertIn("miner_intake_unconfirmed", {issue["signal"] for issue in digest["issues"]})

    def test_traveler_to_miner_handoff_reports_request_missing(self) -> None:
        digest = build_ops_digest(
            status={
                "run_at": "2026-05-10T00:00:00Z",
                "seeds_total": 1,
                "seeds_skipped_cooldown": 1,
                "seeds_with_errors": 0,
                "total_accepted": 0,
            },
            status_path="/tmp/status.json",
            traveler_report_status={
                "miner_request_url_hashes": ["abc"],
            },
            traveler_status_path="/tmp/traveler.json",
            miner_intake_rows=[],
            enable_traveler_handoff=True,
            now=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(digest["handoffs"]["traveler_to_miner"]["status"], "request_missing")
        self.assertIn("miner_request_missing", {issue["signal"] for issue in digest["issues"]})

    def test_traveler_to_miner_handoff_confirms_matching_message(self) -> None:
        digest = build_ops_digest(
            status={
                "run_at": "2026-05-10T00:00:00Z",
                "seeds_total": 1,
                "seeds_skipped_cooldown": 1,
                "seeds_with_errors": 0,
                "total_accepted": 0,
            },
            status_path="/tmp/status.json",
            traveler_report_status={
                "miner_message_id": "123",
                "miner_request_url_hashes": ["468164e75ba0e4cf"],
            },
            traveler_status_path="/tmp/traveler.json",
            miner_intake_rows=[
                {
                    "intake_id": "miner_ok",
                    "url": "https://example.com/research",
                    "discord": {"message_id": 123},
                }
            ],
            enable_traveler_handoff=True,
            now=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        )

        handoff = digest["handoffs"]["traveler_to_miner"]
        self.assertEqual(handoff["status"], "ok")
        self.assertEqual(handoff["matched_intake_ids"], ["miner_ok"])
        self.assertEqual(handoff["missing_count"], 0)

    def test_traveler_to_miner_handoff_treats_preexisting_intake_as_covered(self) -> None:
        digest = build_ops_digest(
            status={
                "run_at": "2026-05-10T00:00:00Z",
                "seeds_total": 1,
                "seeds_skipped_cooldown": 1,
                "seeds_with_errors": 0,
                "total_accepted": 0,
            },
            status_path="/tmp/status.json",
            traveler_report_status={
                "miner_message_id": "new-message",
                "miner_request_url_hashes": ["468164e75ba0e4cf"],
            },
            traveler_status_path="/tmp/traveler.json",
            miner_intake_rows=[
                {
                    "intake_id": "miner_preexisting",
                    "url": "https://example.com/research",
                    "discord": {"message_id": "old-message"},
                }
            ],
            enable_traveler_handoff=True,
            now=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc),
        )

        handoff = digest["handoffs"]["traveler_to_miner"]
        self.assertEqual(handoff["status"], "duplicate_preexisting")
        self.assertEqual(handoff["missing_count"], 0)
        self.assertEqual(handoff["duplicate_or_preexisting_count"], 1)
        self.assertIn("miner_request_duplicate_preexisting", {issue["signal"] for issue in digest["issues"]})


if __name__ == "__main__":
    unittest.main()
