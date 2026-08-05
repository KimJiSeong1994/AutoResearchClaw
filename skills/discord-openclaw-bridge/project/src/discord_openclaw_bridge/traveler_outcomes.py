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

**Miner approval is the optimization label.** Legacy `adopted` events may mean
that a URL appeared anywhere on the collection surface, including intake or a
pending review. The SkillOpt reward therefore uses only an exact-URL
`handed_off` event followed by `miner_approved` from the approved export.
Approvals without that prior handoff are treated as unrelated/orphan evidence.

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
from collections.abc import Iterable
from datetime import datetime, timezone
from math import log2
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .miner import append_jsonl, sanitize_url

LOG = logging.getLogger(__name__)

def _default_workspace() -> Path:
    return Path(
        os.environ.get("HERMES_WORKSPACE")
        or os.environ.get("OPENCLAW_WORKSPACE")
        or str(Path.home() / ".hermes" / "workspace")
    ).expanduser()


DEFAULT_LEDGER_PATH = _default_workspace() / "state" / "traveler-outcome-ledger.jsonl"
EVENT_OBSERVED = "observed"
EVENT_ADOPTED = "adopted"
EVENT_HANDED_OFF = "handed_off"
EVENT_MINER_APPROVED = "miner_approved"
EVENT_MINER_APPROVAL_REVOKED = "miner_approval_revoked"
EVENT_REVIEWED = "reviewed"
SCHEMA_VERSION = "traveler-outcome.v1"
SKILLOPT_REWARD_SCHEMA_VERSION = "traveler-skillopt-reward.v1"

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
    return hashlib.sha256(sanitize_url(url).encode("utf-8")).hexdigest()[:16]


def default_ledger_path() -> Path:
    raw = os.environ.get("JIPHYEONJEON_TRAVELER_OUTCOME_LEDGER_PATH", "").strip()
    return Path(raw).expanduser() if raw else _default_workspace() / "state" / "traveler-outcome-ledger.jsonl"


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
    request = row.get("request") if isinstance(row.get("request"), dict) else {}
    matched = extract.get("matched_keywords")
    return {
        "schema_version": SCHEMA_VERSION,
        "event": EVENT_OBSERVED,
        "url_key": url_key(url),
        "url": url,
        "host": _host(url),
        "provider": str(row.get("provider") or ""),
        "topic_id": str(row.get("topic_id") or request.get("topic_id") or ""),
        "query": str(row.get("query") or request.get("query") or ""),
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


def _event_index(rows: Iterable[dict[str, Any]], event: str) -> set[str]:
    return {str(row.get("url_key") or "") for row in rows if row.get("event") == event and row.get("url_key")}


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
    report_status_path: Path | None = None,
    approved_export_path: Path | None = None,
    miner_review_queue_path: Path | None = None,
    miner_decisions_path: Path | None = None,
) -> dict[str, Any]:
    """Append new observations, adoption events, and any Claw verdicts.

    Idempotent: re-running without new evidence, adoptions, or verdicts writes
    nothing. `decisions` maps candidate_id to the latest source-review decision;
    a verdict is the strong label, whereas adoption is inferred.
    """
    ledger_rows = _read_jsonl_rows(ledger_path)
    seen, adopted = _ledger_index(ledger_rows)
    handed_off = _event_index(ledger_rows, EVENT_HANDED_OFF)
    miner_labels = _latest_miner_labels(ledger_rows)
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

    by_key = {str(row.get("url_key") or ""): row for row in ledger_rows if row.get("event") == EVENT_OBSERVED}
    new_handoffs = 0
    for key in _report_handoff_keys(report_status_path):
        if key in handed_off or key not in by_key:
            continue
        source = by_key[key]
        handed_off_at = _utc_now()
        append_jsonl(
            ledger_path,
            {
                "schema_version": SCHEMA_VERSION,
                "event": EVENT_HANDED_OFF,
                "url_key": key,
                "url": source.get("url", ""),
                "handed_off_at": handed_off_at,
            },
        )
        handed_off.add(key)
        ledger_rows.append({"event": EVENT_HANDED_OFF, "url_key": key, "url": source.get("url", ""), "handed_off_at": handed_off_at})
        new_handoffs += 1

    miner_decisions = _miner_review_decisions(miner_review_queue_path, miner_decisions_path)
    new_miner_approvals = 0
    new_miner_approval_revocations = 0
    for url, decision in miner_decisions.items():
        key = url_key(url)
        verdict = str(decision.get("decision") or "")
        current = miner_labels.get(key, "")
        if verdict == "approve":
            if current == EVENT_MINER_APPROVED or key not in by_key or key not in handed_off:
                continue
            append_jsonl(
                ledger_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": EVENT_MINER_APPROVED,
                    "url_key": key,
                    "url": url,
                    "intake_id": decision.get("intake_id", ""),
                    "decision_id": decision.get("decision_id", ""),
                    "approved_at": decision.get("decided_at") or _utc_now(),
                    "source": "miner_decision_log",
                },
            )
            miner_labels[key] = EVENT_MINER_APPROVED
            new_miner_approvals += 1
            continue
        if verdict in {"reject", "hold"} and current == EVENT_MINER_APPROVED:
            append_jsonl(
                ledger_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "event": EVENT_MINER_APPROVAL_REVOKED,
                    "url_key": key,
                    "url": url,
                    "intake_id": decision.get("intake_id", ""),
                    "decision_id": decision.get("decision_id", ""),
                    "verdict": verdict,
                    "revoked_at": decision.get("decided_at") or _utc_now(),
                    "source": "miner_decision_log",
                },
            )
            miner_labels[key] = EVENT_MINER_APPROVAL_REVOKED
            new_miner_approval_revocations += 1

    # The approved-only export is retained as a compatibility fallback. When a
    # URL has an authoritative decision-log row, that latest decision wins so a
    # stale export cannot resurrect a rejected or held item.
    authoritative_urls = set(miner_decisions)
    for url in _approved_export_urls(approved_export_path) - authoritative_urls:
        key = url_key(url)
        if miner_labels.get(key) in {EVENT_MINER_APPROVED, EVENT_MINER_APPROVAL_REVOKED} or key not in by_key or key not in handed_off:
            continue
        append_jsonl(
            ledger_path,
            {
                "schema_version": SCHEMA_VERSION,
                "event": EVENT_MINER_APPROVED,
                "url_key": key,
                "url": url,
                "approved_at": _utc_now(),
                "source": "approved_export_fallback",
            },
        )
        miner_labels[key] = EVENT_MINER_APPROVED
        new_miner_approvals += 1

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
        "new_handoffs": new_handoffs,
        "new_miner_approvals": new_miner_approvals,
        "new_miner_approval_revocations": new_miner_approval_revocations,
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


def _report_handoff_keys(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    values = payload.get("miner_request_url_hashes") if isinstance(payload, dict) else []
    return {str(value) for value in values if isinstance(value, str) and len(value) == 16}


def _approved_export_urls(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    urls: set[str] = set()
    for row in _read_jsonl_rows(path):
        url = sanitize_url(str(row.get("url") or row.get("source_url") or ""))
        if url:
            urls.add(url)
    return urls


def _miner_review_decisions(queue_path: Path | None, decisions_path: Path | None) -> dict[str, dict[str, Any]]:
    """Return the latest authoritative Miner decision keyed by sanitized URL."""
    if queue_path is None or decisions_path is None or not queue_path.exists() or not decisions_path.exists():
        return {}
    queue = {
        str(row.get("intake_id") or ""): sanitize_url(str(row.get("url") or ""))
        for row in _read_jsonl_rows(queue_path)
        if row.get("intake_id")
    }
    latest_by_url: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl_rows(decisions_path):
        intake_id = str(row.get("intake_id") or "")
        verdict = str(row.get("decision") or "")
        url = queue.get(intake_id, "")
        if url and verdict in {"approve", "reject", "hold"}:
            latest_by_url[url] = row
    return latest_by_url


def _latest_miner_labels(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for row in rows:
        event = str(row.get("event") or "")
        key = str(row.get("url_key") or "")
        if key and event in {EVENT_MINER_APPROVED, EVENT_MINER_APPROVAL_REVOKED}:
            labels[key] = event
    return labels


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_entropy(values: list[str]) -> float:
    values = [value or "unknown" for value in values]
    if len(values) <= 1:
        return 0.0
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    entropy = -sum((count / len(values)) * log2(count / len(values)) for count in counts.values())
    return round(entropy / log2(len(counts)), 4) if len(counts) > 1 else 0.0


def _dominant(values: list[str]) -> str:
    if not values:
        return ""
    counts: dict[str, int] = {}
    for value in values:
        counts[value or "unknown"] = counts.get(value or "unknown", 0) + 1
    value, count = max(counts.items(), key=lambda item: item[1])
    return f"{value}:{round(100 * count / len(values), 1)}%"


def skillopt_reward_report(ledger_path: Path, *, as_of: str = "", approval_window_days: int = 7) -> dict[str, Any]:
    rows = _read_jsonl_rows(ledger_path)
    now = _parse_time(as_of) or datetime.now(timezone.utc)
    observed: dict[str, dict[str, Any]] = {}
    handoff_at: dict[str, datetime] = {}
    latest_miner_labels: dict[str, str] = {}
    legacy_adopted = 0
    for row in rows:
        key = str(row.get("url_key") or "")
        if not key:
            continue
        if row.get("event") == EVENT_OBSERVED and key not in observed:
            observed[key] = row
        elif row.get("event") == EVENT_HANDED_OFF:
            parsed = _parse_time(row.get("handed_off_at"))
            if parsed is not None:
                handoff_at[key] = parsed
        elif row.get("event") == EVENT_MINER_APPROVED:
            latest_miner_labels[key] = EVENT_MINER_APPROVED
        elif row.get("event") == EVENT_MINER_APPROVAL_REVOKED:
            latest_miner_labels[key] = EVENT_MINER_APPROVAL_REVOKED
        elif row.get("event") == EVENT_ADOPTED:
            legacy_adopted += 1

    approved_with_handoff = {
        key for key, label in latest_miner_labels.items() if label == EVENT_MINER_APPROVED and key in handoff_at
    }
    orphan_approved = {
        key for key, label in latest_miner_labels.items() if label == EVENT_MINER_APPROVED and key not in handoff_at
    }
    matured: set[str] = set()
    censored_recent = 0
    for key, when in handoff_at.items():
        if key in approved_with_handoff:
            continue
        age_days = (now - when).total_seconds() / 86400
        if age_days >= approval_window_days:
            matured.add(key)
        else:
            censored_recent += 1
    denominator = approved_with_handoff | matured
    approved_rows = [observed[key] for key in sorted(approved_with_handoff) if key in observed]
    approval_rate = (len(approved_with_handoff) / len(denominator)) if denominator else 0.0

    components = {
        "provider": _normalized_entropy([str(row.get("provider") or "") for row in approved_rows]),
        "domain": _normalized_entropy([str(row.get("host") or _host(str(row.get("url") or ""))) for row in approved_rows]),
        "topic": _normalized_entropy([str(row.get("topic_id") or row.get("query") or "") for row in approved_rows]),
    }
    diversity = round(sum(components.values()) / len(components), 4) if components else 0.0
    score = round(100 * ((0.7 * approval_rate) + (0.3 * diversity)), 2)
    diagnostics: list[str] = []
    if len(denominator) < 5:
        diagnostics.append("insufficient eligible sample: fewer than 5 approved or matured handed-off URLs")
    for name, values in {
        "provider": [str(row.get("provider") or "") for row in approved_rows],
        "domain": [str(row.get("host") or _host(str(row.get("url") or ""))) for row in approved_rows],
        "topic": [str(row.get("topic_id") or row.get("query") or "") for row in approved_rows],
    }.items():
        marker = _dominant(values)
        if marker:
            diagnostics.append(f"{name}_dominance={marker}")

    return {
        "schema_version": SKILLOPT_REWARD_SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "approval_window_days": approval_window_days,
        "eligible_sample_count": len(denominator),
        "approved_url_count": len(approved_with_handoff),
        "orphan_miner_approval_count": len(orphan_approved),
        "censored_recent_unapproved_count": censored_recent,
        "legacy_adopted_ignored_count": legacy_adopted,
        "approval": {
            "approved_count": len(approved_with_handoff),
            "denominator_count": len(denominator),
            "approval_rate_pct": round(100 * approval_rate, 1) if denominator else 0.0,
        },
        "diversity": {
            "approved_url_count": len(approved_rows),
            "normalized_shannon": diversity,
            "components": components,
        },
        "weights": {"miner_approval_rate": 0.7, "approved_diversity": 0.3},
        "reward_score": score,
        "diagnostics": diagnostics,
        "strong_positive_events": [EVENT_MINER_APPROVED],
        "eligible_for_bounded_autotune": len(denominator) >= 5,
        "automatic_apply": {"evaluator_mutates": False, "executed": False},
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
    parser.add_argument("--report-status", type=Path, default=None, help="Traveler collection report status JSON with miner_request_url_hashes.")
    parser.add_argument("--approved-export", type=Path, default=None, help="Miner approved manual links JSONL.")
    parser.add_argument("--miner-review-queue", type=Path, default=None, help="Miner review queue JSONL used to resolve approved intake URLs.")
    parser.add_argument("--miner-decisions", type=Path, default=None, help="Miner append-only review decisions JSONL; latest decision wins.")
    parser.add_argument("--approval-window-days", type=int, default=7)
    parser.add_argument("--skillopt-reward", action="store_true", help="Emit traveler-skillopt-reward.v1 instead of legacy calibration.")
    parser.add_argument("--report-only", action="store_true", help="Report from the existing ledger without recording new events.")
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    ledger = (args.ledger or default_ledger_path()).expanduser()

    if not args.report_only:
        from .traveler_evidence import default_evidence_path
        from .traveler_review import (
            default_source_decisions_path,
            latest_source_decisions,
        )

        evidence = (args.evidence or default_evidence_path()).expanduser()
        collected_urls, collected_hosts = _load_collection_surface()
        summary = record_outcomes(
            evidence_path=evidence,
            ledger_path=ledger,
            collected_urls=collected_urls,
            collected_hosts=collected_hosts,
            decisions=latest_source_decisions(default_source_decisions_path()),
            report_status_path=args.report_status.expanduser() if args.report_status else None,
            approved_export_path=args.approved_export.expanduser() if args.approved_export else None,
            miner_review_queue_path=args.miner_review_queue.expanduser() if args.miner_review_queue else None,
            miner_decisions_path=args.miner_decisions.expanduser() if args.miner_decisions else None,
        )
        LOG.info(json.dumps(summary, ensure_ascii=False, indent=2))

    report = skillopt_reward_report(ledger, approval_window_days=args.approval_window_days) if args.skillopt_reward else calibration_report(ledger)
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
