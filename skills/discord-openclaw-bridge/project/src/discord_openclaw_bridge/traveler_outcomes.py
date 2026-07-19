"""Record whether 집현전-여행자's discoveries were actually adopted, over time.

The traveler scores every candidate it finds, but nothing ever recorded whether
those scores predicted anything. `traveler-collection-report-last-status.json`
is overwritten each run and carries only a count, so there was no way to ask
"do high-confidence discoveries get collected more often than low-confidence
ones?" This module builds the missing history.

Three design decisions worth stating, because the obvious version of this tool
produces confident nonsense:

**Source from evidence, not the candidate queue.** The candidate queue only
contains discoveries that already passed the evidence gate. Calibrating on it
could only ever measure the region the current scoring accepts, so it would
recommend tightening thresholds and never loosening them. `evidence.jsonl`
records rejected candidates too (`traveler_source_discovery` appends before it
filters), which is what makes both directions observable.

**Adoption is URL-exact.** The daily report also grades by host, but host
matching is far too loose to learn from: one arxiv.org seed would mark every
arxiv.org discovery as adopted regardless of topic. Host overlap is recorded
separately as a weaker signal and must not be read as adoption.

**Event-sourced, not a daily snapshot.** A row per candidate per day would be
mostly "still not adopted" — unbounded growth swamping the rare real events.
Each candidate is observed once, and a second row is written only if adoption
is actually detected. Unadopted candidates are right-censored, which is what
the report accounts for.

Known limitation that cannot be engineered away: the operator sees the
traveler's confidence score in the daily report before deciding what to
collect. So "high confidence correlates with adoption" partly measures the
operator trusting the score, not the score being correct. The report states
this rather than pretending otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .miner import append_jsonl, sanitize_url

LOG = logging.getLogger(__name__)

DEFAULT_LEDGER_PATH = Path.home() / ".openclaw" / "workspace" / "state" / "traveler-outcome-ledger.jsonl"
EVENT_OBSERVED = "observed"
EVENT_ADOPTED = "adopted"
EVENT_REVIEWED = "reviewed"
SCHEMA_VERSION = "traveler-outcome.v1"

# Buckets are coarse on purpose: the scorer emits a handful of discrete values
# (0.6, 0.65, ... 0.95), so finer bins would mostly hold one value each.
CONFIDENCE_BUCKETS = ((0.0, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 1.01))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def url_key(url: str) -> str:
    """Stable join key. The ledger keeps the readable URL too; this is for matching."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def default_ledger_path() -> Path:
    raw = os.environ.get("JIPHYEONJEON_TRAVELER_OUTCOME_LEDGER_PATH", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_LEDGER_PATH


def _confidence_bucket(score: float) -> str:
    for low, high in CONFIDENCE_BUCKETS:
        if low <= score < high:
            return f"{low:.2f}-{high:.2f}" if high <= 1.0 else f"{low:.2f}+"
    return "unknown"


def observation_from_evidence(row: dict[str, Any]) -> dict[str, Any] | None:
    """One ledger observation from an evidence record, or None if unusable."""
    url = sanitize_url(str(row.get("url") or ""))
    if not url:
        return None
    decision = row.get("decision") if isinstance(row.get("decision"), dict) else {}
    extract = row.get("extract") if isinstance(row.get("extract"), dict) else {}
    matched = extract.get("matched_keywords")
    return {
        "schema_version": SCHEMA_VERSION,
        "event": EVENT_OBSERVED,
        "url_key": url_key(url),
        "url": url,
        "host": _host(url),
        "provider": str(row.get("provider") or ""),
        "candidate_state": str(decision.get("candidate_state") or ""),
        "rejection_class": str(decision.get("rejection_class") or ""),
        "confidence_score": float(decision.get("confidence_score") or 0.0),
        "matched_keyword_count": len(matched) if isinstance(matched, list) else 0,
        "item_count": int(extract.get("item_count") or 0),
        "discovered_at": str(row.get("fetched_at") or ""),
        "observed_at": _utc_now(),
    }


def _ledger_index(rows: Iterable[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """Return (already observed keys, already adopted keys)."""
    observed: set[str] = set()
    adopted: set[str] = set()
    for row in rows:
        key = str(row.get("url_key") or "")
        if not key:
            continue
        if row.get("event") == EVENT_OBSERVED:
            observed.add(key)
        elif row.get("event") == EVENT_ADOPTED:
            adopted.add(key)
    return observed, adopted


def _reviewed_index(rows: Iterable[dict[str, Any]]) -> set[tuple[str, str]]:
    """(url_key, verdict) pairs already recorded, so a revised verdict still lands."""
    return {
        (str(row.get("url_key") or ""), str(row.get("verdict") or ""))
        for row in rows
        if row.get("event") == EVENT_REVIEWED
    }


def record_outcomes(
    *,
    evidence_path: Path,
    ledger_path: Path,
    collected_urls: set[str],
    collected_hosts: set[str],
    decisions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Append new observations, adoption events, and any Claw verdicts.

    Idempotent: re-running without new evidence, adoptions, or verdicts writes
    nothing. `decisions` maps candidate_id to the latest source-review decision;
    a verdict is the strong label, whereas adoption is inferred.
    """
    ledger_rows = _read_jsonl_rows(ledger_path)
    seen, adopted = _ledger_index(ledger_rows)
    collected_keys = {url_key(url) for url in collected_urls}

    new_observations = 0
    for row in _read_jsonl_rows(evidence_path):
        observation = observation_from_evidence(row)
        if observation is None or observation["url_key"] in seen:
            continue
        append_jsonl(ledger_path, observation)
        seen.add(observation["url_key"])
        ledger_rows.append(observation)
        new_observations += 1

    new_adoptions = 0
    for row in ledger_rows:
        if row.get("event") != EVENT_OBSERVED:
            continue
        key = str(row.get("url_key") or "")
        if key in adopted or key not in collected_keys:
            continue
        append_jsonl(
            ledger_path,
            {
                "schema_version": SCHEMA_VERSION,
                "event": EVENT_ADOPTED,
                "url_key": key,
                "url": row.get("url", ""),
                "observed_at": row.get("observed_at", ""),
                "adopted_at": _utc_now(),
            },
        )
        adopted.add(key)
        new_adoptions += 1

    new_verdicts = 0
    if decisions:
        already = _reviewed_index(ledger_rows)
        by_url_key = {url_key(str(row.get("url") or "")): row for row in decisions.values() if row.get("url")}
        for key, decision in by_url_key.items():
            verdict = str(decision.get("decision") or "")
            if not verdict or key not in seen or (key, verdict) in already:
                continue
            append_jsonl(
                ledger_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": EVENT_REVIEWED,
                    "url_key": key,
                    "url": decision.get("url", ""),
                    "verdict": verdict,
                    "reviewer": decision.get("reviewer", ""),
                    "decided_at": decision.get("decided_at", ""),
                    "recorded_at": _utc_now(),
                },
            )
            new_verdicts += 1

    return {
        "run_at": _utc_now(),
        "ledger_path": str(ledger_path),
        "new_observations": new_observations,
        "new_adoptions": new_adoptions,
        "new_verdicts": new_verdicts,
        "total_observed": len(seen),
        "total_adopted": len(adopted),
        "host_overlap_only": sum(
            1
            for row in ledger_rows
            if row.get("event") == EVENT_OBSERVED
            and str(row.get("url_key")) not in adopted
            and str(row.get("host") or "") in collected_hosts
        ),
    }


def calibration_report(ledger_path: Path) -> dict[str, Any]:
    """Adoption rate by confidence bucket and by the traveler's own verdict.

    Unadopted candidates are censored, not negatives — a recent discovery has
    had less chance to be adopted than an old one. The report reports counts
    rather than pretending to a corrected rate.
    """
    rows = _read_jsonl_rows(ledger_path)
    _, adopted = _ledger_index(rows)
    observations = [row for row in rows if row.get("event") == EVENT_OBSERVED]
    # Latest verdict wins, so a revised review supersedes the earlier one.
    verdicts: dict[str, str] = {
        str(row.get("url_key") or ""): str(row.get("verdict") or "")
        for row in rows
        if row.get("event") == EVENT_REVIEWED
    }

    by_bucket: dict[str, dict[str, int]] = {}
    by_state: dict[str, dict[str, int]] = {}
    by_verdict: dict[str, dict[str, int]] = {}
    for row in observations:
        key = str(row.get("url_key") or "")
        was_adopted = key in adopted
        bucket = _confidence_bucket(float(row.get("confidence_score") or 0.0))
        state = str(row.get("candidate_state") or "unknown")
        for table, name in ((by_bucket, bucket), (by_state, state)):
            entry = table.setdefault(name, {"observed": 0, "adopted": 0})
            entry["observed"] += 1
            entry["adopted"] += int(was_adopted)
        if key in verdicts:
            entry = by_verdict.setdefault(bucket, {"observed": 0, "adopted": 0, "approved": 0, "rejected": 0})
            entry["observed"] += 1
            entry["adopted"] += int(was_adopted)
            if verdicts[key] == "approve":
                entry["approved"] += 1
            elif verdicts[key] == "reject":
                entry["rejected"] += 1

    def with_rate(table: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
        return {
            name: {**counts, "adoption_rate_pct": round(100 * counts["adopted"] / counts["observed"], 1) if counts["observed"] else 0.0}
            for name, counts in sorted(table.items())
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "report_type": "traveler_calibration",
        "generated_at": _utc_now(),
        "total_observed": len(observations),
        "total_adopted": len(adopted),
        "total_reviewed": len(verdicts),
        "by_confidence_bucket": with_rate(by_bucket),
        "by_candidate_state": with_rate(by_state),
        # The strong label: an explicit Claw verdict, not adoption inferred from
        # the collection surface. Prefer this once enough reviews accumulate.
        "reviewed_by_confidence_bucket": {
            name: {**counts, "approval_rate_pct": round(100 * counts["approved"] / counts["observed"], 1) if counts["observed"] else 0.0}
            for name, counts in sorted(by_verdict.items())
        },
        "advisory_only": True,
        "limitations": [
            "unadopted candidates are right-censored, not confirmed negatives; recent discoveries have had less time to be adopted",
            "the operator sees the confidence score in the daily report before deciding, so adoption partly measures trust in the score rather than its accuracy",
            "adoption is URL-exact; host overlap is tracked separately and is not adoption",
            "no automatic tuning: runtime/traveler-scoring.json changes remain a human decision",
            "reviewed_by_confidence_bucket covers only candidates a reviewer has actually ruled on, so it is a biased subset until review coverage is high",
        ],
    }


def _load_collection_surface() -> tuple[set[str], set[str]]:
    """Reuse the daily report's view of what the miner has actually collected."""
    from .post_traveler_collection_report import _load_collection_context

    context = _load_collection_context()
    return (context.seed_urls | context.collected_urls, context.seed_hosts | context.collected_hosts)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-traveler-outcomes",
        description="Record and report whether Traveler discoveries were adopted downstream.",
    )
    parser.add_argument("--evidence", type=Path, default=None, help="Traveler evidence JSONL (default: traveler review evidence path).")
    parser.add_argument("--ledger", type=Path, default=None, help="Outcome ledger JSONL (default: workspace state path).")
    parser.add_argument("--report", type=Path, default=None, help="Write the calibration report JSON here instead of stdout.")
    parser.add_argument("--report-only", action="store_true", help="Report from the existing ledger without recording new events.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    ledger = (args.ledger or default_ledger_path()).expanduser()

    if not args.report_only:
        from .traveler_evidence import default_evidence_path

        from .traveler_review import default_source_decisions_path, latest_source_decisions

        evidence = (args.evidence or default_evidence_path()).expanduser()
        collected_urls, collected_hosts = _load_collection_surface()
        summary = record_outcomes(
            evidence_path=evidence,
            ledger_path=ledger,
            collected_urls=collected_urls,
            collected_hosts=collected_hosts,
            decisions=latest_source_decisions(default_source_decisions_path()),
        )
        LOG.info(json.dumps(summary, ensure_ascii=False, indent=2))

    report = calibration_report(ledger)
    if args.report:
        out = args.report.expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        LOG.info("calibration report written to %s", out)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
