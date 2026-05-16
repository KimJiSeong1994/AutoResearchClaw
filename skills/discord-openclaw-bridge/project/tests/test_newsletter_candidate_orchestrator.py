from __future__ import annotations

import json
from pathlib import Path

from discord_openclaw_bridge.newsletter_candidate_orchestrator import build_newsletter_candidates, main


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def test_newsletter_candidate_orchestrator_uses_approved_only_rows() -> None:
    approved_rows = [
        {"intake_id": "miner_ok", "title": "Approved", "url": "https://example.com/approved", "summary": "summary", "tags": ["approved-by-jiphyeonjeon-claw"], "review": {"source_decision": "approve"}},
        {"intake_id": "miner_rejected", "title": "Rejected", "url": "https://example.com/rejected", "tags": ["approved-by-jiphyeonjeon-claw"], "review": {"source_decision": "approve"}},
    ]
    queue_rows = [
        {"intake_id": "miner_ok", "title": "Approved", "url": "https://example.com/approved"},
        {"intake_id": "miner_rejected", "title": "Rejected", "url": "https://example.com/rejected"},
        {"intake_id": "miner_pending", "title": "Pending", "url": "https://example.com/pending"},
    ]
    decisions_rows = [
        {"intake_id": "miner_ok", "decision": "approve", "decision_id": "review_ok"},
        {"intake_id": "miner_rejected", "decision": "reject", "decision_id": "review_no"},
    ]

    candidates = build_newsletter_candidates(
        approved_rows=approved_rows,
        queue_rows=queue_rows,
        decisions_rows=decisions_rows,
    )

    assert [row["source_intake_id"] for row in candidates] == ["miner_ok"]
    assert candidates[0]["candidate_status"] == "needs_editorial_review"
    assert candidates[0]["source_decision_id"] == "review_ok"
    assert candidates[0]["safety"]["publish_ready"] is False
    assert candidates[0]["safety"]["writes_newsletter_archive"] is False
    assert candidates[0]["safety"]["writes_card_news_source"] is False


def test_newsletter_candidate_orchestrator_rejects_tampered_approved_url() -> None:
    candidates = build_newsletter_candidates(
        approved_rows=[
            {
                "intake_id": "miner_ok",
                "title": "Tampered",
                "url": "https://evil.example.com/not-approved",
                "tags": ["approved-by-jiphyeonjeon-claw"],
                "review": {"source_decision": "approve"},
            }
        ],
        queue_rows=[{"intake_id": "miner_ok", "title": "Approved", "url": "https://example.com/approved"}],
        decisions_rows=[{"intake_id": "miner_ok", "decision": "approve", "decision_id": "review_ok"}],
    )

    assert candidates == []


def test_newsletter_candidate_orchestrator_dry_run_writes_nothing(tmp_path: Path, capsys) -> None:
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    approved = tmp_path / "approved.jsonl"
    output = tmp_path / "candidate-review.jsonl"
    _write_jsonl(queue, [{"intake_id": "miner_ok", "title": "Approved", "url": "https://example.com/approved"}])
    _write_jsonl(decisions, [{"intake_id": "miner_ok", "decision": "approve", "decision_id": "review_ok"}])
    _write_jsonl(approved, [{"intake_id": "miner_ok", "title": "Approved", "url": "https://example.com/approved", "tags": ["approved-by-jiphyeonjeon-claw"], "review": {"source_decision": "approve"}}])

    assert main(["--queue", str(queue), "--decisions", str(decisions), "--approved", str(approved), "--output", str(output), "--dry-run"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate_count"] == 1
    assert not output.exists()
