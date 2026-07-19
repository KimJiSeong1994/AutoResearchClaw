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
    EVENT_OBSERVED,
    calibration_report,
    observation_from_evidence,
    record_outcomes,
    url_key,
)


def evidence_row(url: str, *, state: str = "accepted", score: float = 0.75, keywords: int = 2, provider: str = "static-technical-sources") -> dict[str, Any]:
    return {
        "url": url,
        "provider": provider,
        "fetched_at": "2026-07-01T00:00:00Z",
        "decision": {"candidate_state": state, "confidence_score": score, "rejection_class": "" if state == "accepted" else "low_relevance"},
        "extract": {"matched_keywords": ["k"] * keywords, "item_count": 3},
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
