"""The tuner must refuse far more often than it fires.

The dangerous failure here is not a wrong recommendation, it is a confident one
built on a handful of rows that quietly narrows what the traveler may find. So
most of these tests assert that nothing happens: an unobserved source, silence
mistaken for rejection, a source the record supports keeping, an emptied
portfolio, a stale baseline.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from discord_openclaw_bridge.traveler_tuning import apply_proposals, propose_changes, summarize_ledger
from discord_openclaw_bridge.traveler_outcomes import url_key

GOOD = "https://good.example.com/feed"
BAD = "https://bad.example.com/feed"


def scoring_config(sources: list[list[str]] | None = None, override_types: list[str] | None = None) -> dict[str, Any]:
    return {
        "evidence_scoring": {"base_confidence": 0.6},
        "curated_static_override": {"confidence_score": 0.55, "source_types": override_types or ["article_hub", "research_lab_blog"]},
        "static_sources": sources
        if sources is not None
        else [
            ["Good", GOOD, "article_hub", "note"],
            ["Bad", BAD, "article_hub", "note"],
        ],
    }


def write_ledger(path: Path, *, url: str, observations: int, verdict: str | None = None, adopted: bool = False) -> None:
    rows: list[dict[str, Any]] = []
    for i in range(observations):
        rows.append({"event": "observed", "url_key": url_key(url), "url": url, "provider": "static-technical-sources", "confidence_score": 0.7, "observed_at": f"2026-07-{i + 1:02d}T00:00:00Z"})
    if adopted:
        rows.append({"event": "adopted", "url_key": url_key(url), "url": url, "adopted_at": "2026-07-20T00:00:00Z"})
    if verdict:
        rows.append({"event": "reviewed", "url_key": url_key(url), "url": url, "verdict": verdict, "decided_at": "2026-07-20T00:00:00Z"})
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_empty_ledger_proposes_nothing(tmp_path: Path) -> None:
    """The state right after shipping: no data, so no recommendations."""
    report = propose_changes(tmp_path / "missing.jsonl", scoring_config())
    assert report["proposals"] == []
    assert report["sources_with_outcomes"] == 0
    assert {r["reason"] for r in report["refusals"]} == {"never_observed"}


def test_source_never_seen_in_the_ledger_is_refused(tmp_path: Path) -> None:
    """A config row with no matching discovery must not be dropped on no data."""
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=GOOD, observations=3, verdict="approve")

    report = propose_changes(ledger, scoring_config())

    assert report["proposals"] == []
    assert any(r["target"] == BAD and r["reason"] == "never_observed" for r in report["refusals"])


def test_unreviewed_source_is_never_dropped(tmp_path: Path) -> None:
    """Silence is not rejection — the single most tempting wrong inference."""
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=BAD, observations=20)

    report = propose_changes(ledger, scoring_config())

    assert report["proposals"] == []
    assert any(r["target"] == BAD and r["reason"] == "unreviewed" for r in report["refusals"])


def test_sustained_rejection_proposes_dropping_the_source(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=BAD, observations=4, verdict="reject")

    report = propose_changes(ledger, scoring_config())

    dropped = [p for p in report["proposals"] if p["action"] == "drop_static_source"]
    assert [p["target"] for p in dropped] == [BAD]
    assert dropped[0]["evidence"]["rejected"] == 1
    assert report["automatic_apply"] is False


def test_approved_source_is_kept_even_with_many_observations(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=GOOD, observations=6, verdict="approve")

    report = propose_changes(ledger, scoring_config())

    assert [p["target"] for p in report["proposals"] if p["action"] == "drop_static_source"] == []


def test_adopted_source_is_kept_even_without_approval(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=BAD, observations=4, verdict="reject")
    write_ledger(ledger, url=BAD, observations=1, adopted=True)

    report = propose_changes(ledger, scoring_config())

    assert [p["target"] for p in report["proposals"] if p["action"] == "drop_static_source"] == []


def test_confidence_weights_are_never_proposed(tmp_path: Path) -> None:
    """They do not gate anything, so tuning them would be theatre."""
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=BAD, observations=4, verdict="reject")

    report = propose_changes(ledger, scoring_config())

    targets = {str(p["target"]) for p in report["proposals"]}
    assert not any("confidence" in t or "evidence_scoring" in t for t in targets)
    assert any("confidence weights are deliberately not tuned" in note for note in report["notes"])


def test_apply_requires_a_matching_baseline(tmp_path: Path) -> None:
    scoring_path = tmp_path / "scoring.json"
    scoring_path.write_text(json.dumps(scoring_config()), encoding="utf-8")
    proposals = [{"action": "drop_static_source", "target": BAD}]

    with pytest.raises(ValueError, match="changed since proposal"):
        apply_proposals(scoring_path=scoring_path, proposals=proposals, baseline_sha256="0" * 64)


def test_apply_drops_the_source_and_keeps_a_backup(tmp_path: Path) -> None:
    import hashlib

    scoring_path = tmp_path / "scoring.json"
    scoring_path.write_text(json.dumps(scoring_config()), encoding="utf-8")
    baseline = hashlib.sha256(scoring_path.read_bytes()).hexdigest()
    lineage = tmp_path / "lineage.jsonl"

    record = apply_proposals(
        scoring_path=scoring_path,
        proposals=[{"action": "drop_static_source", "target": BAD}],
        baseline_sha256=baseline,
        lineage_path=lineage,
    )

    after = json.loads(scoring_path.read_text(encoding="utf-8"))
    assert [row[1] for row in after["static_sources"]] == [GOOD]
    assert record["dropped_sources"] == [BAD]
    assert Path(record["backup_path"]).exists(), "a rollback copy must survive"
    assert json.loads(lineage.read_text(encoding="utf-8").strip())["after_sha256"] == record["after_sha256"]


def test_apply_refuses_to_empty_the_portfolio(tmp_path: Path) -> None:
    """An empty portfolio removes the fallback used when providers rate-limit."""
    import hashlib

    scoring_path = tmp_path / "scoring.json"
    scoring_path.write_text(json.dumps(scoring_config(sources=[["Only", BAD, "article_hub", "note"]])), encoding="utf-8")
    baseline = hashlib.sha256(scoring_path.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="refusing to empty the static portfolio"):
        apply_proposals(scoring_path=scoring_path, proposals=[{"action": "drop_static_source", "target": BAD}], baseline_sha256=baseline)

    assert len(json.loads(scoring_path.read_text(encoding="utf-8"))["static_sources"]) == 1


def test_apply_refuses_an_empty_proposal_list(tmp_path: Path) -> None:
    import hashlib

    scoring_path = tmp_path / "scoring.json"
    scoring_path.write_text(json.dumps(scoring_config()), encoding="utf-8")

    with pytest.raises(ValueError, match="no proposals"):
        apply_proposals(scoring_path=scoring_path, proposals=[], baseline_sha256=hashlib.sha256(scoring_path.read_bytes()).hexdigest())


def test_summary_attributes_outcomes_per_source(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    write_ledger(ledger, url=GOOD, observations=3, verdict="approve", adopted=True)
    write_ledger(ledger, url=BAD, observations=2, verdict="reject")

    summary = summarize_ledger(ledger)

    assert summary[GOOD]["observations"] == 3
    assert summary[GOOD]["approved"] == 1 and summary[GOOD]["adopted"] == 1
    assert summary[BAD]["rejected"] == 1 and summary[BAD]["adopted"] == 0
