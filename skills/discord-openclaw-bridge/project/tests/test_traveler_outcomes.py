"""The outcome ledger must record both directions, and not invent signal.

An adversarial review of the first design named three ways this tool produces
confident nonsense. Each has a test here so the fix cannot be undone quietly:

- sourcing from the candidate queue instead of evidence hides rejected
  candidates, so calibration could only ever recommend tightening thresholds;
- host-level adoption marks every arxiv.org discovery adopted the moment one
  arxiv.org seed exists;
- a daily snapshot grows without bound while adoption events stay rare.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from discord_openclaw_bridge.traveler_outcomes import (
    EVENT_ADOPTED,
    EVENT_HANDED_OFF,
    EVENT_MINER_APPROVAL_REVOKED,
    EVENT_MINER_APPROVED,
    EVENT_OBSERVED,
    calibration_report,
    observation_from_evidence,
    record_outcomes,
    skillopt_reward_report,
    url_key,
)


def evidence_row(url: str, *, state: str = "accepted", score: float = 0.75, keywords: int = 2, provider: str = "static-technical-sources") -> dict[str, Any]:
    return {
        "url": url,
        "provider": provider,
        "fetched_at": "2026-07-01T00:00:00Z",
        "decision": {"candidate_state": state, "confidence_score": score, "rejection_class": "" if state == "accepted" else "low_relevance"},
        "extract": {"matched_keywords": ["k"] * keywords, "item_count": 3},
        "request": {"topic_id": "rag_eval", "query": "RAG evaluation benchmark"},
    }


def write_evidence(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_rejected_candidates_are_recorded_not_dropped(tmp_path: Path) -> None:
    """Without rejected rows the report can only ever say "tighten thresholds"."""
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    write_evidence(
        evidence,
        [
            evidence_row("https://a.example.com/x", state="accepted", score=0.8),
            evidence_row("https://b.example.com/y", state="rejected", score=0.0),
        ],
    )

    summary = record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())

    states = {row["candidate_state"] for row in ledger_rows(ledger) if row["event"] == EVENT_OBSERVED}
    assert states == {"accepted", "rejected"}, "rejected candidates must enter the denominator"
    assert summary["new_observations"] == 2


def test_adoption_is_url_exact_not_host_level(tmp_path: Path) -> None:
    """One arxiv seed must not mark every arxiv discovery adopted."""
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    write_evidence(evidence, [evidence_row("https://arxiv.org/list/cs.RO/recent")])

    summary = record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls={"https://arxiv.org/list/cs.AI/recent"},
        collected_hosts={"arxiv.org"},
    )

    assert summary["new_adoptions"] == 0, "different path on a collected host is not adoption"
    assert summary["host_overlap_only"] == 1, "host overlap must still be visible, just not as adoption"
    assert not [row for row in ledger_rows(ledger) if row["event"] == EVENT_ADOPTED]


def test_exact_url_match_records_an_adoption_event(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    url = "https://arxiv.org/list/cs.AI/recent"
    write_evidence(evidence, [evidence_row(url)])

    record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())
    summary = record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls={url}, collected_hosts={"arxiv.org"})

    adoptions = [row for row in ledger_rows(ledger) if row["event"] == EVENT_ADOPTED]
    assert summary["new_adoptions"] == 1
    assert len(adoptions) == 1
    assert adoptions[0]["url_key"] == url_key(url)
    assert adoptions[0]["adopted_at"]


def test_report_handoff_and_miner_approval_are_strong_events(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    report_status = tmp_path / "traveler-collection-report-last-status.json"
    approved = tmp_path / "approved-manual-links.jsonl"
    url = "https://approved.example.com/paper"
    other = "https://not-requested.example.com/paper"
    write_evidence(evidence, [evidence_row(url), evidence_row(other)])
    report_status.write_text(json.dumps({"miner_request_url_hashes": [url_key(url)], "run_at": "2026-07-02T00:00:00Z"}), encoding="utf-8")
    approved.write_text(json.dumps({"url": url, "approved_at": "2026-07-04T00:00:00Z"}, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls={other},
        collected_hosts={"not-requested.example.com"},
        report_status_path=report_status,
        approved_export_path=approved,
    )

    rows = ledger_rows(ledger)
    assert summary["new_handoffs"] == 1
    assert summary["new_miner_approvals"] == 1
    assert [row["url_key"] for row in rows if row["event"] == EVENT_HANDED_OFF] == [url_key(url)]
    assert [row["url_key"] for row in rows if row["event"] == EVENT_MINER_APPROVED] == [url_key(url)]
    assert [row["url_key"] for row in rows if row["event"] == EVENT_ADOPTED] == [url_key(other)], "legacy adoption remains compatibility-only"


def test_miner_decision_log_is_authoritative_without_approved_export(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    report_status = tmp_path / "traveler-collection-report-last-status.json"
    queue = tmp_path / "link-review-queue.jsonl"
    decisions = tmp_path / "link-review-decisions.jsonl"
    url = "https://approved.example.com/direct-decision"
    intake_id = "miner_direct"
    write_evidence(evidence, [evidence_row(url)])
    report_status.write_text(json.dumps({"miner_request_url_hashes": [url_key(url)]}), encoding="utf-8")
    queue.write_text(json.dumps({"intake_id": intake_id, "url": url}) + "\n", encoding="utf-8")
    decisions.write_text(
        json.dumps(
            {
                "decision_id": "review_direct",
                "intake_id": intake_id,
                "decision": "approve",
                "decided_at": "2026-07-04T00:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls=set(),
        collected_hosts=set(),
        report_status_path=report_status,
        miner_review_queue_path=queue,
        miner_decisions_path=decisions,
    )

    approval = next(row for row in ledger_rows(ledger) if row["event"] == EVENT_MINER_APPROVED)
    assert summary["new_miner_approvals"] == 1
    assert approval["source"] == "miner_decision_log"
    assert approval["decision_id"] == "review_direct"
    assert approval["approved_at"] == "2026-07-04T00:00:00Z"


def test_latest_miner_rejection_revokes_approval_and_stale_export_cannot_restore_it(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    report_status = tmp_path / "report.json"
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    approved_export = tmp_path / "approved.jsonl"
    url = "https://approved.example.com/revised"
    intake_id = "miner_revised"
    write_evidence(evidence, [evidence_row(url)])
    report_status.write_text(json.dumps({"miner_request_url_hashes": [url_key(url)]}), encoding="utf-8")
    queue.write_text(json.dumps({"intake_id": intake_id, "url": url}) + "\n", encoding="utf-8")
    approved_export.write_text(json.dumps({"url": url}) + "\n", encoding="utf-8")
    decisions.write_text(json.dumps({"intake_id": intake_id, "decision": "approve"}) + "\n", encoding="utf-8")
    record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls=set(),
        collected_hosts=set(),
        report_status_path=report_status,
        approved_export_path=approved_export,
        miner_review_queue_path=queue,
        miner_decisions_path=decisions,
    )
    decisions.write_text(
        json.dumps({"intake_id": intake_id, "decision": "approve"})
        + "\n"
        + json.dumps({"intake_id": intake_id, "decision": "reject", "decided_at": "2026-07-05T00:00:00Z"})
        + "\n",
        encoding="utf-8",
    )

    summary = record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls=set(),
        collected_hosts=set(),
        report_status_path=report_status,
        approved_export_path=approved_export,
        miner_review_queue_path=queue,
        miner_decisions_path=decisions,
    )
    reward = skillopt_reward_report(ledger, as_of="2026-08-20T00:00:00Z")

    decisions.unlink()
    fallback_only_summary = record_outcomes(
        evidence_path=evidence,
        ledger_path=ledger,
        collected_urls=set(),
        collected_hosts=set(),
        report_status_path=report_status,
        approved_export_path=approved_export,
        miner_review_queue_path=queue,
        miner_decisions_path=decisions,
    )
    fallback_only_reward = skillopt_reward_report(ledger, as_of="2026-08-20T00:00:00Z")

    assert summary["new_miner_approval_revocations"] == 1
    assert [row["event"] for row in ledger_rows(ledger)].count(EVENT_MINER_APPROVED) == 1
    assert [row["event"] for row in ledger_rows(ledger)].count(EVENT_MINER_APPROVAL_REVOKED) == 1
    assert reward["approval"]["approved_count"] == 0
    assert reward["eligible_sample_count"] == 1, "revoked handoff matures as an unapproved outcome"
    assert fallback_only_summary["new_miner_approvals"] == 0, "a stale export must not resurrect a durable revocation"
    assert fallback_only_reward["approval"]["approved_count"] == 0


def test_skillopt_reward_ignores_legacy_adopted_and_censors_recent_unapproved(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    approved = "https://approved.example.com/paper"
    matured = "https://matured.example.com/paper"
    recent = "https://recent.example.com/paper"
    legacy = "https://legacy.example.com/paper"
    rows = [
        {"event": EVENT_OBSERVED, "url_key": url_key(approved), "url": approved, "provider": "arxiv", "host": "approved.example.com", "topic_id": "rag", "query": "RAG", "observed_at": "2026-07-01T00:00:00Z"},
        {"event": EVENT_HANDED_OFF, "url_key": url_key(approved), "url": approved, "handed_off_at": "2026-07-02T00:00:00Z"},
        {"event": EVENT_MINER_APPROVED, "url_key": url_key(approved), "url": approved, "approved_at": "2026-07-03T00:00:00Z"},
        {"event": EVENT_OBSERVED, "url_key": url_key(matured), "url": matured, "provider": "semantic_scholar", "host": "matured.example.com", "topic_id": "agents", "query": "agents", "observed_at": "2026-07-01T00:00:00Z"},
        {"event": EVENT_HANDED_OFF, "url_key": url_key(matured), "url": matured, "handed_off_at": "2026-07-02T00:00:00Z"},
        {"event": EVENT_OBSERVED, "url_key": url_key(recent), "url": recent, "provider": "arxiv", "host": "recent.example.com", "topic_id": "infra", "query": "infra", "observed_at": "2026-07-09T00:00:00Z"},
        {"event": EVENT_HANDED_OFF, "url_key": url_key(recent), "url": recent, "handed_off_at": "2026-07-09T00:00:00Z"},
        {"event": EVENT_OBSERVED, "url_key": url_key(legacy), "url": legacy, "provider": "static", "host": "legacy.example.com", "topic_id": "legacy", "query": "legacy", "observed_at": "2026-07-01T00:00:00Z"},
        {"event": EVENT_ADOPTED, "url_key": url_key(legacy), "url": legacy, "adopted_at": "2026-07-03T00:00:00Z"},
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = skillopt_reward_report(ledger, as_of="2026-07-10T00:00:00Z", approval_window_days=7)

    assert report["schema_version"] == "traveler-skillopt-reward.v1"
    assert report["eligible_sample_count"] == 2, "approved + matured handed-off only; recent and legacy are censored"
    assert report["approval"]["approved_count"] == 1
    assert report["approval"]["approval_rate_pct"] == 50.0
    assert report["censored_recent_unapproved_count"] == 1
    assert report["legacy_adopted_ignored_count"] == 1
    assert report["weights"] == {"miner_approval_rate": 0.7, "approved_diversity": 0.3}
    assert report["strong_positive_events"] == [EVENT_MINER_APPROVED]
    assert report["eligible_for_bounded_autotune"] is False
    assert "insufficient" in " ".join(report["diagnostics"]).lower()


def test_orphan_miner_approval_without_handoff_is_excluded_from_reward(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    url = "https://orphan.example.com/paper"
    key = url_key(url)
    rows = [
        {"event": EVENT_OBSERVED, "url_key": key, "url": url, "provider": "arxiv", "host": "orphan.example.com", "topic_id": "rag", "query": "RAG", "observed_at": "2026-07-01T00:00:00Z"},
        {"event": EVENT_MINER_APPROVED, "url_key": key, "url": url, "approved_at": "2026-07-03T00:00:00Z"},
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = skillopt_reward_report(ledger, as_of="2026-07-10T00:00:00Z", approval_window_days=7)

    assert report["approval"]["approved_count"] == 0
    assert report["orphan_miner_approval_count"] == 1
    assert report["eligible_sample_count"] == 0


def test_repeated_runs_do_not_grow_the_ledger(tmp_path: Path) -> None:
    """No "still not adopted" rows: unadopted candidates stay censored, not re-logged."""
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    write_evidence(evidence, [evidence_row(f"https://x{i}.example.com/a") for i in range(5)])

    record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())
    after_first = len(ledger_rows(ledger))
    for _ in range(4):
        record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())

    assert after_first == 5
    assert len(ledger_rows(ledger)) == 5, "re-running must not append repeat observations"


def test_adoption_is_recorded_once_even_if_still_collected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    url = "https://a.example.com/x"
    write_evidence(evidence, [evidence_row(url)])

    for _ in range(3):
        record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls={url}, collected_hosts=set())

    assert len([row for row in ledger_rows(ledger) if row["event"] == EVENT_ADOPTED]) == 1


def test_unsafe_or_empty_urls_are_skipped(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    write_evidence(evidence, [{"url": "", "decision": {}}, {"url": "javascript:alert(1)", "decision": {}}])

    summary = record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())

    assert summary["new_observations"] == 0
    assert ledger_rows(ledger) == []


def test_observation_carries_the_scoring_inputs() -> None:
    observation = observation_from_evidence(evidence_row("https://a.example.com/x", score=0.85, keywords=3))
    assert observation is not None
    assert observation["confidence_score"] == 0.85
    assert observation["matched_keyword_count"] == 3
    assert observation["item_count"] == 3
    assert observation["provider"] == "static-technical-sources"
    assert observation["host"] == "a.example.com"


def test_calibration_splits_by_confidence_and_verdict(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    adopted_url = "https://high.example.com/a"
    write_evidence(
        evidence,
        [
            evidence_row(adopted_url, score=0.85),
            evidence_row("https://high2.example.com/a", score=0.85),
            evidence_row("https://low.example.com/a", state="rejected", score=0.0),
        ],
    )
    record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls=set(), collected_hosts=set())
    record_outcomes(evidence_path=evidence, ledger_path=ledger, collected_urls={adopted_url}, collected_hosts=set())

    report = calibration_report(ledger)

    assert report["total_observed"] == 3
    assert report["total_adopted"] == 1
    assert report["by_confidence_bucket"]["0.80+"] == {"observed": 2, "adopted": 1, "adoption_rate_pct": 50.0}
    assert report["by_confidence_bucket"]["0.00-0.60"] == {"observed": 1, "adopted": 0, "adoption_rate_pct": 0.0}
    assert report["by_candidate_state"]["rejected"]["observed"] == 1
    assert report["by_candidate_state"]["rejected"]["adopted"] == 0


def test_report_states_its_limitations_and_stays_advisory() -> None:
    report = calibration_report(Path("/nonexistent/ledger.jsonl"))
    assert report["advisory_only"] is True
    assert report["total_observed"] == 0
    joined = " ".join(report["limitations"]).lower()
    assert "censored" in joined, "censoring must be disclosed or the rates read as ground truth"
    assert "operator" in joined, "the operator-sees-the-score circularity must be disclosed"
    assert "no automatic tuning" in joined
