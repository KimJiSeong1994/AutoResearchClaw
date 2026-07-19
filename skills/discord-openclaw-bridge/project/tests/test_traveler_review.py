"""Verdict capture, and — the part that actually matters — its consumers.

A decisions file nothing reads is worse than no decisions file: the operator
approves a candidate, sees it again in tomorrow's report, and learns the review
does nothing. So most of this module tests that a recorded verdict actually
changes what the operator sees and what the outcome ledger knows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from discord_openclaw_bridge.post_traveler_collection_report import _candidate_rows
from discord_openclaw_bridge.traveler_outcomes import EVENT_REVIEWED, calibration_report, record_outcomes
from discord_openclaw_bridge.traveler_review import (
    TERMINAL_DECISIONS,
    decided_candidate_ids,
    latest_source_decisions,
    pending_candidates,
    record_source_decision,
)


def candidate(candidate_id: str, url: str, *, title: str = "A source") -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "url": url,
        "title": title,
        "status": "pending_source_review",
        "source_type": "article_hub",
        "created_at": "2026-07-01T00:00:00Z",
        "evidence": {"status": "fetched", "summary": "s", "confidence": 0.8},
    }


def write_queue(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_decision_is_appended_without_mutating_the_queue(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/feed")])
    before = queue.read_text(encoding="utf-8")

    row = record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve", reason="recurring feed")

    assert row["candidate_id"] == "c1"
    assert row["decision"] == "approve"
    assert row["url"] == "https://a.example.com/feed"
    assert row["decided_at"].endswith("Z")
    assert queue.read_text(encoding="utf-8") == before, "the queue must stay append-only and untouched"
    assert len(read_rows(decisions)) == 1


def test_unknown_candidate_is_refused(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/feed")])

    # Match the message: without the explicit guard, the dict lookup below still
    # raises KeyError, so a bare `raises(KeyError)` would pass either way.
    with pytest.raises(KeyError, match="unknown candidate_id"):
        record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="nope", decision="approve")
    assert read_rows(decisions) == []


def test_invalid_decision_is_refused(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/feed")])

    with pytest.raises(ValueError):
        record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="maybe")  # type: ignore[arg-type]
    assert read_rows(decisions) == []


def test_unsafe_url_in_queue_is_refused(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "javascript:alert(1)")])

    with pytest.raises(ValueError):
        record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")


def test_latest_verdict_supersedes_the_earlier_one(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/feed")])

    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="hold")
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")

    assert latest_source_decisions(decisions)["c1"]["decision"] == "approve"
    assert len(read_rows(decisions)) == 2, "history must be preserved, not overwritten"


def test_hold_is_not_terminal_but_approve_and_reject_are(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/1"), candidate("c2", "https://b.example.com/2"), candidate("c3", "https://c.example.com/3")])

    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c2", decision="reject")
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c3", decision="hold")

    assert TERMINAL_DECISIONS == frozenset({"approve", "reject"})
    assert decided_candidate_ids(decisions) == {"c1", "c2"}
    pending_ids = {row["candidate_id"] for row in pending_candidates(queue, decisions)}
    assert pending_ids == {"c3"}, "hold means revisit, so it stays pending"


def test_decided_candidates_leave_the_daily_report(tmp_path: Path) -> None:
    """The consumer that makes this more than a write-only log."""
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/1"), candidate("c2", "https://b.example.com/2")])

    assert len(_candidate_rows(queue, decisions)) == 2
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")

    remaining = _candidate_rows(queue, decisions)
    assert [row["candidate_id"] for row in remaining] == ["c2"], "an approved candidate must stop reappearing"


def test_held_candidates_still_appear_in_the_daily_report(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    write_queue(queue, [candidate("c1", "https://a.example.com/1")])
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="hold")

    assert [row["candidate_id"] for row in _candidate_rows(queue, decisions)] == ["c1"]


def test_verdicts_reach_the_outcome_ledger_as_a_strong_label(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    url = "https://a.example.com/feed"
    write_queue(queue, [candidate("c1", url)])
    evidence.write_text(
        json.dumps({"url": url, "provider": "static", "decision": {"candidate_state": "accepted", "confidence_score": 0.85}, "extract": {"matched_keywords": ["k"], "item_count": 1}})
        + "\n",
        encoding="utf-8",
    )
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")

    record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())
    summary = record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls=set(),
        collected_hosts=set(),
        decisions=latest_source_decisions(decisions),
    )

    reviewed = [row for row in read_rows(ledger) if row["event"] == EVENT_REVIEWED]
    assert summary["new_verdicts"] == 1
    assert len(reviewed) == 1
    assert reviewed[0]["verdict"] == "approve"

    report = calibration_report(ledger)
    assert report["total_reviewed"] == 1
    assert report["reviewed_by_confidence_bucket"]["0.80+"]["approved"] == 1
    assert report["reviewed_by_confidence_bucket"]["0.80+"]["approval_rate_pct"] == 100.0


def test_verdict_is_not_recorded_twice_but_a_revision_is(tmp_path: Path) -> None:
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    url = "https://a.example.com/feed"
    write_queue(queue, [candidate("c1", url)])
    evidence.write_text(json.dumps({"url": url, "decision": {"candidate_state": "accepted", "confidence_score": 0.8}, "extract": {}}) + "\n", encoding="utf-8")
    record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())

    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="hold")
    for _ in range(3):
        record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set(), decisions=latest_source_decisions(decisions))
    assert len([r for r in read_rows(ledger) if r["event"] == EVENT_REVIEWED]) == 1

    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")
    summary = record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set(), decisions=latest_source_decisions(decisions))

    assert summary["new_verdicts"] == 1, "a revised verdict must be recorded"
    verdicts = [r["verdict"] for r in read_rows(ledger) if r["event"] == EVENT_REVIEWED]
    assert verdicts == ["hold", "approve"]


def test_verdict_for_an_unobserved_url_is_skipped(tmp_path: Path) -> None:
    """Ledger verdicts hang off observations; a verdict alone is not an outcome row."""
    queue = tmp_path / "candidates.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    write_queue(queue, [candidate("c1", "https://unobserved.example.com/x")])
    evidence.write_text("", encoding="utf-8")
    record_source_decision(queue_path=queue, decisions_path=decisions, candidate_id="c1", decision="approve")

    summary = record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set(), decisions=latest_source_decisions(decisions))

    assert summary["new_verdicts"] == 0
    assert read_rows(ledger) == []
