"""Read-only fan-in audit digest for 집현전-감사팀.

The audit team observes already-produced manifests, snapshots, and JSON/JSONL
artifacts.  It does not approve content, publish to Discord, mutate cron, restart
services, or remediate production state.  Optional writes are append-only issue
and audit ledgers that live outside content review queues.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .miner import _append_jsonl_unlocked, locked_jsonl_paths, read_jsonl

TEAM_ID = "jiphyeonjeon-audit-team"
DEFAULT_WORKSPACE = Path.home() / ".openclaw" / "workspace"
DEFAULT_ISSUE_QUEUE_PATH = DEFAULT_WORKSPACE / "review" / "jiphyeonjeon-audit" / "issues.jsonl"
DEFAULT_AUDIT_LOG_PATH = DEFAULT_WORKSPACE / "logs" / "jiphyeonjeon-audit" / "audit-log.jsonl"
DEFAULT_REVIEW_QUEUE_PATH = DEFAULT_WORKSPACE / "review" / "jiphyeonjeon-claw" / "link-review-queue.jsonl"
DEFAULT_DECISIONS_PATH = DEFAULT_WORKSPACE / "review" / "jiphyeonjeon-claw" / "link-review-decisions.jsonl"
DEFAULT_CANDIDATE_PATH = DEFAULT_WORKSPACE / "review" / "newsletter-candidates" / "candidate-review.jsonl"
DEFAULT_CARD_NEWS_AUDIT_PATH = DEFAULT_WORKSPACE / "logs" / "discord-card-news" / "card-news-publication-audit.jsonl"
DEFAULT_TRUST_GATE_REPORT_DIR = DEFAULT_WORKSPACE / "reports" / "jiphyeonjeon-trust-gates"

AUDIT_SUITE_NAMES = (
    "schedule_cron_drift",
    "discord_liveness_log_lag",
    "scheduled_backlog_sla",
    "trust_gate_incidents",
    "provenance_schema",
    "api_rate_budget",
)
DANGEROUS_AUDIT_TARGET_NAMES = {
    "link-review-queue.jsonl",
    "link-review-decisions.jsonl",
    "candidate-review.jsonl",
    "approved-manual-links.jsonl",
    "card-news-publication-audit.jsonl",
}
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{12,}"),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+"),
    re.compile(r"(?i)(api[_-]?key|bot[_-]?token|webhook[_-]?url|relay[_-]?read[_-]?token)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _hash(value: Any, length: int = 16) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:length]


def _redact_text(value: str) -> str:
    out = value.replace(str(Path.home()), "~")
    out = re.sub(r"/Users/[^/]+", "~", out)
    out = re.sub(r"/home/ubuntu", "~", out)
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in value.items()}
    return value


def _severity_status(issues: list[dict[str, Any]]) -> str:
    if any(issue.get("severity") == "error" for issue in issues):
        return "error"
    if issues:
        return "warning"
    return "ok"


def _issue(
    *,
    suite: str,
    signal: str,
    severity: str,
    evidence_refs: dict[str, Any] | list[Any] | str,
    recommended_action: str,
    observed_at: str,
    snapshot_observed_at: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    evidence = _redact(evidence_refs)
    issue_key = {"suite": suite, "signal": signal, "evidence": evidence}
    return {
        "schema_version": 1,
        "issue_id": "audit_" + _hash(issue_key),
        "team_id": TEAM_ID,
        "suite": suite,
        "signal": signal,
        "severity": severity,
        "observed_at": observed_at,
        "snapshot_observed_at": snapshot_observed_at,
        "evidence_refs": evidence,
        "recommended_action": _redact_text(recommended_action),
        "message": _redact_text(message or signal),
        "no_mutation": True,
    }


def _audit_event(
    *,
    issue: dict[str, Any] | None,
    suite: str,
    signal: str,
    severity: str,
    observed_at: str,
    result: str,
    evidence_refs: Any,
    snapshot_observed_at: str | None = None,
) -> dict[str, Any]:
    evidence = _redact(evidence_refs)
    return {
        "schema_version": 1,
        "event_id": "audit_event_" + _hash({"suite": suite, "signal": signal, "observed_at": observed_at, "evidence": evidence}),
        "team_id": TEAM_ID,
        "suite": suite,
        "signal": signal,
        "severity": severity,
        "observed_at": observed_at,
        "snapshot_observed_at": snapshot_observed_at,
        "evidence_refs": evidence,
        "issue_id": issue.get("issue_id") if issue else None,
        "result": result,
        "redaction_applied": True,
        "no_mutation": True,
    }


def _audit_event_for_issue(issue: dict[str, Any]) -> dict[str, Any]:
    return _audit_event(
        issue=issue,
        suite=str(issue["suite"]),
        signal=str(issue["signal"]),
        severity=str(issue["severity"]),
        observed_at=str(issue["observed_at"]),
        snapshot_observed_at=issue.get("snapshot_observed_at"),
        result="issue",
        evidence_refs=issue.get("evidence_refs", {}),
    )


def _ok_event(
    *,
    suite: str,
    observed_at: str,
    evidence_refs: Any,
    snapshot_observed_at: str | None = None,
) -> dict[str, Any]:
    return _audit_event(
        issue=None,
        suite=suite,
        signal="ok",
        severity="info",
        observed_at=observed_at,
        result="ok",
        evidence_refs=evidence_refs,
        snapshot_observed_at=snapshot_observed_at,
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON artifact is not an object: {path}")
    return raw


def _read_jsonl_required(path: Path, *, suite: str, observed_at: str, label: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not path.exists():
        return [], [
            _issue(
                suite=suite,
                signal="missing_artifact",
                severity="warning",
                observed_at=observed_at,
                evidence_refs={"source_path": str(path), "artifact": label},
                recommended_action="Create or refresh the configured audit input artifact before trusting this suite result.",
            )
        ]
    return read_jsonl(path), []


def _validate_audit_write_path(path: Path, *, kind: str) -> None:
    raw = str(path.expanduser().resolve(strict=False))
    if kind == "issue":
        allowed = f"{os.sep}review{os.sep}jiphyeonjeon-audit{os.sep}"
    else:
        allowed = f"{os.sep}logs{os.sep}jiphyeonjeon-audit{os.sep}"
    if path.name in DANGEROUS_AUDIT_TARGET_NAMES or allowed not in raw:
        raise ValueError(f"audit {kind} path must stay under an audit-owned root: {path}")


def _snapshot_issue(snapshot: dict[str, Any] | None, *, source_path: str, suite: str, observed_at: datetime, default_max_age_seconds: int = 3600) -> tuple[dict[str, Any] | None, str | None]:
    observed_iso = _iso(observed_at)
    if snapshot is None:
        return _issue(
            suite=suite,
            signal="snapshot_missing",
            severity="warning",
            observed_at=observed_iso,
            evidence_refs={"source_path": source_path},
            recommended_action="Generate the sanitized status snapshot before relying on this audit suite.",
        ), None
    raw_time = snapshot.get("snapshot_observed_at") or snapshot.get("generated_at") or snapshot.get("run_at")
    snap_time = _parse_utc(raw_time)
    if snap_time is None:
        return _issue(
            suite=suite,
            signal="snapshot_unparseable",
            severity="error",
            observed_at=observed_iso,
            evidence_refs={"source_path": source_path, "snapshot_observed_at": raw_time},
            recommended_action="Rewrite the status snapshot with a UTC ISO8601 snapshot_observed_at timestamp.",
        ), None
    max_age = int(snapshot.get("max_snapshot_age_seconds") or default_max_age_seconds)
    if observed_at - snap_time > timedelta(seconds=max_age):
        snap_iso = _iso(snap_time)
        return _issue(
            suite=suite,
            signal="snapshot_stale",
            severity="warning",
            observed_at=observed_iso,
            snapshot_observed_at=snap_iso,
            evidence_refs={"source_path": source_path, "max_snapshot_age_seconds": max_age},
            recommended_action="Refresh the sanitized status snapshot and inspect the producer if staleness persists.",
        ), snap_iso
    return None, _iso(snap_time)


def _decided_intake_ids(decisions_rows: Iterable[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in decisions_rows:
        if str(row.get("decision") or "") in {"approve", "reject", "hold"} and str(row.get("intake_id") or ""):
            out.add(str(row.get("intake_id")))
    return out


def _suite_schedule_cron_drift(*, jobs_path: Path, wrappers: list[Path], cron_snapshot_path: Path | None, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    observed = _iso(now)
    if not jobs_path.exists():
        issues.append(_issue(suite="schedule_cron_drift", signal="runtime_jobs_missing", severity="error", observed_at=observed, evidence_refs={"source_path": str(jobs_path)}, recommended_action="Restore runtime/jobs.yaml before running scheduler audit."))
    for wrapper in wrappers:
        if not wrapper.exists():
            issues.append(_issue(suite="schedule_cron_drift", signal="scheduler.runner_missing", severity="error", observed_at=observed, evidence_refs={"source_path": str(wrapper)}, recommended_action="Restore the committed stable runner wrapper or update runtime manifest deliberately."))
        elif not os.access(wrapper, os.R_OK):
            issues.append(_issue(suite="schedule_cron_drift", signal="scheduler.runner_unreadable", severity="error", observed_at=observed, evidence_refs={"source_path": str(wrapper)}, recommended_action="Fix runner file permissions."))
        elif shutil.which("bash"):
            proc = subprocess.run(["bash", "-n", str(wrapper)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, check=False)
            if proc.returncode != 0:
                issues.append(_issue(suite="schedule_cron_drift", signal="scheduler.runner_syntax_invalid", severity="error", observed_at=observed, evidence_refs={"source_path": str(wrapper), "error": (proc.stderr or "").splitlines()[:1]}, recommended_action="Fix committed runner shell syntax before installing or relying on cron."))
    if cron_snapshot_path is not None:
        snapshot = _read_json(cron_snapshot_path)
        snap_issue, snap_iso = _snapshot_issue(snapshot, source_path=str(cron_snapshot_path), suite="schedule_cron_drift", observed_at=now, default_max_age_seconds=86400)
        if snap_issue:
            issues.append(snap_issue)
        elif snapshot is not None:
            text = str(snapshot.get("crontab") or snapshot.get("content") or "")
            for marker in snapshot.get("expected_markers", []) if isinstance(snapshot.get("expected_markers"), list) else []:
                if str(marker) and str(marker) not in text:
                    issues.append(_issue(suite="schedule_cron_drift", signal="scheduler.missing_cron", severity="warning", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(cron_snapshot_path), "marker": str(marker)}, recommended_action="Inspect the installed user crontab against runtime/jobs.yaml."))
            for key in ("last_status_at", "last_log_at"):
                raw_inner = snapshot.get(key)
                inner = _parse_utc(raw_inner)
                max_inner = int(snapshot.get("max_inner_age_seconds") or snapshot.get("max_snapshot_age_seconds") or 86400)
                if raw_inner and inner is None:
                    issues.append(_issue(suite="schedule_cron_drift", signal="snapshot_unparseable", severity="error", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(cron_snapshot_path), "field": key}, recommended_action="Write scheduler snapshot inner timestamps as UTC ISO8601."))
                elif inner and now - inner > timedelta(seconds=max_inner):
                    issues.append(_issue(suite="schedule_cron_drift", signal=f"scheduler.{key}_stale", severity="warning", observed_at=observed, snapshot_observed_at=_iso(inner), evidence_refs={"source_path": str(cron_snapshot_path), "field": key, "max_inner_age_seconds": max_inner}, recommended_action="Inspect the corresponding cron runner/status/log producer."))
    if not issues:
        events.append(
            _ok_event(
                suite="schedule_cron_drift",
                observed_at=observed,
                evidence_refs={"jobs_path": str(jobs_path), "wrapper_count": len(wrappers)},
            )
        )
    return issues, events


def _suite_liveness(*, snapshot_path: Path | None, now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if snapshot_path is None:
        return [], []
    observed = _iso(now)
    snapshot = _read_json(snapshot_path)
    snap_issue, snap_iso = _snapshot_issue(snapshot, source_path=str(snapshot_path), suite="discord_liveness_log_lag", observed_at=now, default_max_age_seconds=1800)
    if snap_issue:
        return [snap_issue], []
    issues: list[dict[str, Any]] = []
    if snapshot and str(snapshot.get("status") or "").lower() in {"error", "failed"}:
        issues.append(_issue(suite="discord_liveness_log_lag", signal="service_status_error", severity="error", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(snapshot_path), "status": snapshot.get("status")}, recommended_action="Inspect sanitized service logs and Discord bot readiness."))
    if snapshot:
        max_lag = int(snapshot.get("max_liveness_age_seconds") or snapshot.get("max_snapshot_age_seconds") or 1800)
        for key in ("ready_at", "last_heartbeat_at", "last_log_at"):
            raw_inner = snapshot.get(key)
            if not raw_inner:
                continue
            inner = _parse_utc(raw_inner)
            if inner is None:
                issues.append(_issue(suite="discord_liveness_log_lag", signal="snapshot_unparseable", severity="error", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(snapshot_path), "field": key}, recommended_action="Write liveness timestamps as UTC ISO8601."))
            elif now - inner > timedelta(seconds=max_lag):
                issues.append(_issue(suite="discord_liveness_log_lag", signal=f"{key}_stale", severity="warning", observed_at=observed, snapshot_observed_at=_iso(inner), evidence_refs={"source_path": str(snapshot_path), "field": key, "max_liveness_age_seconds": max_lag}, recommended_action="Inspect Discord bot readiness and event-loop health."))
    return issues, [] if issues else [
        _ok_event(
            suite="discord_liveness_log_lag",
            observed_at=observed,
            evidence_refs={"source_path": str(snapshot_path)},
            snapshot_observed_at=snap_iso,
        )
    ]


def _suite_backlog(*, queue_rows: list[dict[str, Any]], decisions_rows: list[dict[str, Any]], queue_path: Path, now: datetime, max_pending_count: int, max_pending_age_days: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed = _iso(now)
    decided = _decided_intake_ids(decisions_rows)
    pending = [row for row in queue_rows if str(row.get("intake_id") or "") and str(row.get("intake_id")) not in decided]
    issues: list[dict[str, Any]] = []
    if len(pending) > max_pending_count:
        issues.append(_issue(suite="scheduled_backlog_sla", signal="pending_count_high", severity="warning", observed_at=observed, evidence_refs={"source_path": str(queue_path), "pending_count": len(pending), "threshold": max_pending_count}, recommended_action="Schedule Jiphyeonjeon-Claw review or explicitly raise the backlog threshold."))
    oldest_age = 0.0
    oldest_at: str | None = None
    for row in pending:
        created = _parse_utc(row.get("created_at"))
        if created is None:
            continue
        age = (now - created).total_seconds() / 86400
        if age > oldest_age:
            oldest_age = age
            oldest_at = _iso(created)
    if oldest_at and oldest_age > max_pending_age_days:
        issues.append(_issue(suite="scheduled_backlog_sla", signal="pending_age_high", severity="warning", observed_at=observed, evidence_refs={"source_path": str(queue_path), "oldest_pending_at": oldest_at, "age_days": round(oldest_age, 2), "threshold_days": max_pending_age_days}, recommended_action="Review or hold stale pending items so inclusion remains intentional."))
    return issues, [] if issues else [
        _ok_event(
            suite="scheduled_backlog_sla",
            observed_at=observed,
            evidence_refs={"source_path": str(queue_path), "pending_count": len(pending)},
        )
    ]


def _trust_records_from_summaries(report_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    if not report_dir.exists():
        return records, failures
    for path in sorted(report_dir.glob("*.json")):
        try:
            raw = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            failures.append({"source_path": str(path), "error_type": type(exc).__name__})
            continue
        if not raw:
            continue
        raw.setdefault("summary_path", str(path))
        if "generated_at" not in raw:
            raw["generated_at"] = _iso(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
        records.append(raw)
    return records, failures


def _suite_trust_gate(*, audit_rows: list[dict[str, Any]], report_dir: Path, now: datetime, window_days: int = 7, repeat_threshold: int = 3) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed = _iso(now)
    issues: list[dict[str, Any]] = []
    summary_records, summary_failures = _trust_records_from_summaries(report_dir)
    if audit_rows:
        # Card-news audit JSONL is the preferred history source.  Trust summary
        # files are still scanned for malformed JSON, but valid summaries are
        # fallback records only to avoid double-counting the same block.
        records = [row for row in audit_rows if isinstance(row, dict)]
    else:
        records = summary_records
    for failure in summary_failures:
        issues.append(_issue(suite="trust_gate_incidents", signal="trust_summary_unparseable", severity="error", observed_at=observed, evidence_refs=failure, recommended_action="Fix or remove malformed trust-gate summary JSON after preserving the bad artifact for diagnosis."))
    block_records = []
    unparseable = False
    for row in records:
        decision = str(row.get("decision") or row.get("status") or "").lower()
        reason_codes = row.get("reason_codes") if isinstance(row.get("reason_codes"), list) else row.get("reasons")
        reasons = sorted(str(item) for item in reason_codes) if isinstance(reason_codes, list) else []
        if "block" not in decision and decision not in {"fail", "failed"}:
            continue
        when = _parse_utc(row.get("generated_at") or row.get("observed_at") or row.get("created_at"))
        if when is None:
            unparseable = True
            continue
        block_records.append((when, decision, reasons, row))
        issues.append(_issue(suite="trust_gate_incidents", signal="trust_gate_block", severity="info", observed_at=observed, snapshot_observed_at=_iso(when), evidence_refs={"surface": row.get("surface") or "card-news", "reason_codes": reasons, "summary_path": row.get("summary_path") or row.get("path")}, recommended_action="Treat this as a protected publication block; inspect evidence quality rather than bypassing the gate."))
    if unparseable:
        issues.append(_issue(suite="trust_gate_incidents", signal="snapshot_unparseable", severity="error", observed_at=observed, evidence_refs={"source_path": str(report_dir)}, recommended_action="Ensure trust gate summaries include generated_at or provide a documented snapshot mtime fallback."))
    window_start = now - timedelta(days=window_days)
    counts: dict[str, int] = {}
    for when, decision, reasons, row in block_records:
        if when < window_start:
            continue
        key = _hash({"surface": row.get("surface") or "card-news", "decision": decision, "reason_codes": reasons, "artifact": row.get("artifact_hash") or row.get("summary_path") or row.get("path")})
        counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        if count >= repeat_threshold:
            issues.append(_issue(suite="trust_gate_incidents", signal="trust_gate_repeated_block", severity="warning", observed_at=observed, evidence_refs={"dedup_key": key, "count": count, "window_days": window_days}, recommended_action="Open a manual editorial/quality review for the repeated trust-gate block reason."))
    return issues, [] if issues else [
        _ok_event(
            suite="trust_gate_incidents",
            observed_at=observed,
            evidence_refs={"record_count": len(records)},
        )
    ]


def _suite_provenance(*, intake_rows: list[dict[str, Any]], decision_rows: list[dict[str, Any]], approved_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]], card_news_rows: list[dict[str, Any]], runtime_manifest_paths: list[Path], wiki_pages: list[Path], now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed = _iso(now)
    issues: list[dict[str, Any]] = []
    for row in intake_rows:
        if not all(row.get(key) for key in ("intake_id", "url", "source")) or not (row.get("created_at") or row.get("published_at")):
            issues.append(_issue(suite="provenance_schema", signal="miner_intake_schema_missing", severity="error", observed_at=observed, evidence_refs={"intake_id": row.get("intake_id"), "url_hash": _hash(str(row.get("url") or ""))}, recommended_action="Repair the intake producer schema; do not promote rows with incomplete provenance."))
    for row in decision_rows:
        if str(row.get("decision") or "") in {"approve", "reject", "hold"}:
            has_id = bool(row.get("decision_id") or (row.get("intake_id") and row.get("decision")))
            has_time = bool(row.get("reviewed_at") or row.get("decided_at") or row.get("created_at"))
            if not (has_id and row.get("intake_id") and has_time and row.get("reviewer")):
                issues.append(_issue(suite="provenance_schema", signal="claw_decision_schema_missing", severity="error", observed_at=observed, evidence_refs={"decision": row.get("decision"), "intake_id": row.get("intake_id")}, recommended_action="Ensure Claw decision rows preserve decision id/key, reviewer, intake linkage, and review timestamp."))
    for row in approved_rows:
        if not (row.get("url") and (row.get("intake_id") or row.get("approved_decision_ref") or isinstance(row.get("review"), dict))):
            issues.append(_issue(suite="provenance_schema", signal="approved_manual_link_schema_missing", severity="error", observed_at=observed, evidence_refs={"url_hash": _hash(str(row.get("url") or "")), "intake_id": row.get("intake_id")}, recommended_action="Approved manual links must retain URL and approval provenance."))
    for row in candidate_rows:
        if row.get("publish_ready") is True:
            issues.append(_issue(suite="provenance_schema", signal="candidate_publish_ready_forbidden", severity="error", observed_at=observed, evidence_refs={"candidate_id": row.get("candidate_id")}, recommended_action="Keep newsletter candidates at needs_editorial_review until a separate promotion workflow acts."))
        has_provenance = bool(row.get("provenance") or row.get("approved_decision_ref") or row.get("source_decision_id") or row.get("source_intake_id"))
        if not (row.get("candidate_id") and row.get("candidate_status") == "needs_editorial_review" and row.get("url") and has_provenance):
            issues.append(_issue(suite="provenance_schema", signal="newsletter_candidate_schema_missing", severity="error", observed_at=observed, evidence_refs={"candidate_id": row.get("candidate_id"), "url_hash": _hash(str(row.get("url") or ""))}, recommended_action="Regenerate candidate artifacts from approved Claw exports with explicit provenance."))
    for row in card_news_rows:
        if row and not ((row.get("decision") or row.get("status")) and (row.get("created_at") or row.get("generated_at") or row.get("observed_at"))):
            issues.append(_issue(suite="provenance_schema", signal="card_news_audit_schema_missing", severity="error", observed_at=observed, evidence_refs={"surface": row.get("surface"), "decision": row.get("decision")}, recommended_action="Card-news audit rows must preserve decision/status and timestamp."))
    for manifest_path in runtime_manifest_paths:
        if manifest_path.exists():
            body = manifest_path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(body) for pattern in SECRET_PATTERNS):
                issues.append(_issue(suite="provenance_schema", signal="runtime_manifest_secret_value", severity="error", observed_at=observed, evidence_refs={"source_path": str(manifest_path)}, recommended_action="Remove concrete secret values from runtime manifests."))
    for page in wiki_pages:
        if page.exists() and 'trust_status: "unreviewed-generated"' not in page.read_text(encoding="utf-8", errors="replace"):
            issues.append(_issue(suite="provenance_schema", signal="wiki_generated_marker_missing", severity="warning", observed_at=observed, evidence_refs={"source_path": str(page)}, recommended_action="Add visible generated-content provenance before treating the wiki page as reviewed."))
    return issues, [] if issues else [
        _ok_event(
            suite="provenance_schema",
            observed_at=observed,
            evidence_refs={"candidate_count": len(candidate_rows)},
        )
    ]


def _suite_api_rate_budget(*, provider_status_paths: list[Path], now: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    observed = _iso(now)
    issues: list[dict[str, Any]] = []
    rate_limit_events: dict[str, list[datetime]] = {}
    for path in provider_status_paths:
        status = _read_json(path)
        snap_issue, snap_iso = _snapshot_issue(status, source_path=str(path), suite="api_rate_budget", observed_at=now, default_max_age_seconds=86400)
        if snap_issue:
            issues.append(snap_issue)
            continue
        if not status:
            continue
        code = int(status.get("status_code") or status.get("http_status") or 0)
        provider = str(status.get("provider") or path.stem)
        fallback_success = bool(status.get("fallback_success") or status.get("report_success"))
        event_time = _parse_utc(status.get("snapshot_observed_at") or status.get("generated_at")) or now
        if code == 429:
            rate_limit_events.setdefault(provider, []).append(event_time)
        if code == 429 and fallback_success:
            issues.append(_issue(suite="api_rate_budget", signal="provider_rate_limited_degraded", severity="warning", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(path), "provider": provider, "status_code": code}, recommended_action="Review provider cooldown/backoff settings; collection continued through fallback."))
        elif code == 429 or str(status.get("status") or "").lower() in {"failed", "error"}:
            issues.append(_issue(suite="api_rate_budget", signal="provider_failure", severity="error", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(path), "provider": provider, "status_code": code}, recommended_action="Inspect provider credentials, rate limit budget, and retry caps without printing secret values."))
        if status.get("report_success") is False or status.get("report_path_missing") is True:
            issues.append(_issue(suite="api_rate_budget", signal="provider_no_report", severity="error", observed_at=observed, snapshot_observed_at=snap_iso, evidence_refs={"source_path": str(path), "provider": provider}, recommended_action="Inspect why provider failure prevented a status/report artifact."))
    window_start = now - timedelta(days=7)
    for provider, events_for_provider in rate_limit_events.items():
        recent = [item for item in events_for_provider if item >= window_start]
        if len(recent) >= 3:
            issues.append(_issue(suite="api_rate_budget", signal="provider_repeated_rate_limit", severity="warning", observed_at=observed, evidence_refs={"provider": provider, "count": len(recent), "window_days": 7}, recommended_action="Review provider rate-limit budget and cooldown settings."))
    return issues, [] if issues else [
        _ok_event(
            suite="api_rate_budget",
            observed_at=observed,
            evidence_refs={"provider_status_count": len(provider_status_paths)},
        )
    ]


def build_audit_digest(
    *,
    now: datetime | None = None,
    runtime_jobs_path: Path = Path("runtime/jobs.yaml"),
    scheduler_wrappers: list[Path] | None = None,
    cron_snapshot_path: Path | None = None,
    liveness_snapshot_path: Path | None = None,
    review_queue_path: Path = DEFAULT_REVIEW_QUEUE_PATH,
    decisions_path: Path = DEFAULT_DECISIONS_PATH,
    intake_path: Path | None = None,
    approved_path: Path | None = None,
    candidate_path: Path = DEFAULT_CANDIDATE_PATH,
    card_news_audit_path: Path = DEFAULT_CARD_NEWS_AUDIT_PATH,
    trust_gate_report_dir: Path = DEFAULT_TRUST_GATE_REPORT_DIR,
    provider_status_paths: list[Path] | None = None,
    wiki_pages: list[Path] | None = None,
    max_pending_count: int = 20,
    max_pending_age_days: float = 7.0,
) -> dict[str, Any]:
    current = (now or _utc_now()).astimezone(timezone.utc)
    issues: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    suites: dict[str, str] = {}

    observed = _iso(current)
    queue_rows, queue_missing = _read_jsonl_required(review_queue_path, suite="scheduled_backlog_sla", observed_at=observed, label="review_queue")
    decision_rows, decision_missing = _read_jsonl_required(decisions_path, suite="provenance_schema", observed_at=observed, label="decisions")
    candidate_rows, candidate_missing = _read_jsonl_required(candidate_path, suite="provenance_schema", observed_at=observed, label="newsletter_candidates")
    card_rows, card_missing = _read_jsonl_required(card_news_audit_path, suite="trust_gate_incidents", observed_at=observed, label="card_news_audit")
    suite_calls = [
        _suite_schedule_cron_drift(jobs_path=runtime_jobs_path, wrappers=scheduler_wrappers or [], cron_snapshot_path=cron_snapshot_path, now=current),
        _suite_liveness(snapshot_path=liveness_snapshot_path, now=current),
        (queue_missing + decision_missing, []),
        _suite_backlog(queue_rows=queue_rows, decisions_rows=decision_rows, queue_path=review_queue_path, now=current, max_pending_count=max_pending_count, max_pending_age_days=max_pending_age_days),
        (card_missing, []),
        _suite_trust_gate(audit_rows=card_rows, report_dir=trust_gate_report_dir, now=current),
        (candidate_missing, []),
        _suite_provenance(intake_rows=read_jsonl(intake_path) if intake_path else [], decision_rows=decision_rows, approved_rows=read_jsonl(approved_path) if approved_path else [], candidate_rows=candidate_rows, card_news_rows=card_rows, runtime_manifest_paths=[runtime_jobs_path, Path("runtime/agents.yaml")], wiki_pages=wiki_pages or [], now=current),
        _suite_api_rate_budget(provider_status_paths=provider_status_paths or [], now=current),
    ]
    for suite_issues, suite_events in suite_calls:
        issues.extend(suite_issues)
        events.extend(suite_events)
    for name in AUDIT_SUITE_NAMES:
        suite_issue_count = sum(1 for issue in issues if issue.get("suite") == name)
        suites[name] = "warning" if suite_issue_count else "ok"
        if any(issue.get("suite") == name and issue.get("severity") == "error" for issue in issues):
            suites[name] = "error"
    for issue in issues:
        events.append(_audit_event_for_issue(issue))
    digest = {
        "team_id": TEAM_ID,
        "agent_id": TEAM_ID,
        "generated_at": _iso(current),
        "health_status": _severity_status(issues),
        "summary": {"issue_count": len(issues), "audit_event_count": len(events)},
        "suites": suites,
        "issues": issues,
        "audit_events": events,
        "no_mutation": True,
    }
    return _redact(digest)


def write_issue_queue(path: Path, digest: dict[str, Any]) -> int:
    _validate_audit_write_path(path, kind="issue")
    issues = [issue for issue in digest.get("issues", []) if isinstance(issue, dict)]
    if not issues:
        return 0
    appended = 0
    with locked_jsonl_paths(path):
        existing_ids = {str(row.get("issue_id") or "") for row in read_jsonl(path) if isinstance(row, dict)}
        for issue in issues:
            issue_id = str(issue.get("issue_id") or "")
            if not issue_id or issue_id in existing_ids:
                continue
            _append_jsonl_unlocked(path, {"issue_id": issue_id, "team_id": TEAM_ID, "status": "open", "first_seen": digest.get("generated_at"), "last_seen": digest.get("generated_at"), "source": "audit_team_digest", "issue": issue, "no_mutation": True})
            existing_ids.add(issue_id)
            appended += 1
    return appended


def write_audit_log(path: Path, digest: dict[str, Any]) -> int:
    _validate_audit_write_path(path, kind="log")
    events = [event for event in digest.get("audit_events", []) if isinstance(event, dict)]
    if not events:
        return 0
    with locked_jsonl_paths(path):
        for event in events:
            _append_jsonl_unlocked(path, event)
    return len(events)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="discord-openclaw-audit-team", description="Build a read-only Jiphyeonjeon Audit Team digest.")
    parser.add_argument("--runtime-jobs-path", type=Path, default=Path("runtime/jobs.yaml"))
    parser.add_argument("--scheduler-wrapper", action="append", type=Path, default=[])
    parser.add_argument("--cron-snapshot-path", type=Path, default=None)
    parser.add_argument("--liveness-snapshot-path", type=Path, default=None)
    parser.add_argument("--review-queue-path", type=Path, default=Path(os.getenv("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", str(DEFAULT_REVIEW_QUEUE_PATH))))
    parser.add_argument("--decisions-path", type=Path, default=Path(os.getenv("JIPHYEONJEON_MINER_DECISIONS_PATH", str(DEFAULT_DECISIONS_PATH))))
    parser.add_argument("--intake-path", type=Path, default=None)
    parser.add_argument("--approved-path", type=Path, default=None)
    parser.add_argument("--candidate-path", type=Path, default=Path(os.getenv("JIPHYEONJEON_NEWSLETTER_CANDIDATE_PATH", str(DEFAULT_CANDIDATE_PATH))))
    parser.add_argument("--card-news-audit-path", type=Path, default=Path(os.getenv("CARD_NEWS_PUBLICATION_AUDIT_PATH", str(DEFAULT_CARD_NEWS_AUDIT_PATH))))
    parser.add_argument("--trust-gate-report-dir", type=Path, default=Path(os.getenv("JIPHYEONJEON_TRUST_GATE_REPORT_DIR", str(DEFAULT_TRUST_GATE_REPORT_DIR))))
    parser.add_argument("--provider-status-path", action="append", type=Path, default=[])
    parser.add_argument("--wiki-page", action="append", type=Path, default=[])
    parser.add_argument("--issue-queue-path", type=Path, default=Path(os.getenv("JIPHYEONJEON_AUDIT_ISSUE_QUEUE_PATH", str(DEFAULT_ISSUE_QUEUE_PATH))))
    parser.add_argument("--audit-log-path", type=Path, default=Path(os.getenv("JIPHYEONJEON_AUDIT_LOG_PATH", str(DEFAULT_AUDIT_LOG_PATH))))
    parser.add_argument("--max-pending-count", type=int, default=20)
    parser.add_argument("--max-pending-age-days", type=float, default=7.0)
    parser.add_argument("--write-issue-queue", action="store_true")
    parser.add_argument("--write-audit-log", action="store_true")
    parser.add_argument("--fail-on-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        digest = build_audit_digest(
            runtime_jobs_path=args.runtime_jobs_path.expanduser(),
            scheduler_wrappers=[path.expanduser() for path in args.scheduler_wrapper],
            cron_snapshot_path=args.cron_snapshot_path.expanduser() if args.cron_snapshot_path else None,
            liveness_snapshot_path=args.liveness_snapshot_path.expanduser() if args.liveness_snapshot_path else None,
            review_queue_path=args.review_queue_path.expanduser(),
            decisions_path=args.decisions_path.expanduser(),
            intake_path=args.intake_path.expanduser() if args.intake_path else None,
            approved_path=args.approved_path.expanduser() if args.approved_path else None,
            candidate_path=args.candidate_path.expanduser(),
            card_news_audit_path=args.card_news_audit_path.expanduser(),
            trust_gate_report_dir=args.trust_gate_report_dir.expanduser(),
            provider_status_paths=[path.expanduser() for path in args.provider_status_path],
            wiki_pages=[path.expanduser() for path in args.wiki_page],
            max_pending_count=args.max_pending_count,
            max_pending_age_days=args.max_pending_age_days,
        )
        if args.write_issue_queue:
            digest["summary"]["issue_queue_appended"] = write_issue_queue(args.issue_queue_path.expanduser(), digest)
        if args.write_audit_log:
            digest["summary"]["audit_log_appended"] = write_audit_log(args.audit_log_path.expanduser(), digest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        now = _iso(_utc_now())
        issue = _issue(suite="artifact_integrity", signal="read_failed", severity="error", observed_at=now, evidence_refs={"error": str(exc)}, recommended_action="Inspect JSON artifact syntax and filesystem permissions.")
        digest = {"team_id": TEAM_ID, "agent_id": TEAM_ID, "generated_at": now, "health_status": "error", "summary": {"issue_count": 1}, "issues": [issue], "audit_events": [], "no_mutation": True}
    print(json.dumps(_redact(digest), ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_error and digest.get("health_status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
