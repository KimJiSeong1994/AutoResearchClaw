#!/usr/bin/env python3
"""Read-only SkillOpt operational evaluator for Jiphyeonjeon Traveler.

The evaluator consumes sanitized JSON snapshots emitted by the Traveler scout,
source-discovery, and collection-report jobs.  It emits a deterministic status
record and never mutates queues, configs, Discord state, or runtime manifests.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from skillopt_common import read_json, write_json
except ModuleNotFoundError:  # pragma: no cover - direct path fallback in tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from skillopt_common import read_json, write_json

SCHEMA_VERSION = "skillopt-traveler-ops.v1"
STATUS_EXIT = {"ok": 0, "degraded": 1, "failed": 2}
DEFAULT_MAX_AGE_HOURS = 36.0
MAX_FUTURE_SKEW_SECONDS = 300.0

FORBIDDEN_RE = re.compile(
    r"(?i)(/Users/|Mobile Documents|discord(?:app)?\.com/api/webhooks/|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]+|"
    r"api[_ -]?key|bot[_ -]?token|private email body|mailbox-only)"
)
LEGACY_PRODUCTION_PATH_RE = re.compile(r"(?i)(?:^|[~/])\.openclaw/workspace(?:/|$)")
HOME_HERMES_RE = re.compile(r"(?i)(?:~|\$HOME|/home/[^/\s]+)/(?:\.hermes/workspace)(?:/|$)")
HOME_OPENCLAW_RE = re.compile(r"(?i)(?:~|\$HOME|/home/[^/\s]+)/(?:\.openclaw/workspace)(?:/|$)")
TRAVELER_PATH_KEYS = {
    "workspace",
    "research_queue_path",
    "scout_queue_path",
    "candidate_queue_path",
    "evidence_path",
    "source_queue_path",
    "status_path",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
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


def evaluate_freshness(
    snapshots: dict[str, dict[str, Any]],
    *,
    as_of: str = "",
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> tuple[list[str], dict[str, Any]]:
    now = parse_timestamp(as_of) if as_of else datetime.now(timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    freshness: dict[str, Any] = {
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "max_age_hours": max_age_hours,
        "max_future_skew_seconds": MAX_FUTURE_SKEW_SECONDS,
        "artifacts": {},
    }
    reasons: list[str] = []
    max_age_seconds = max(0.0, float(max_age_hours)) * 3600.0
    for label in ("scout", "discovery", "report"):
        value = snapshots.get(label, {}).get("run_at")
        parsed = parse_timestamp(value)
        artifact: dict[str, Any] = {"run_at": sanitize_text(str(value or ""))}
        if not value:
            reasons.append(f"missing_{label}_run_at")
            artifact["status"] = "missing"
        elif parsed is None:
            reasons.append(f"invalid_{label}_run_at")
            artifact["status"] = "invalid"
        else:
            age_seconds = (now - parsed).total_seconds()
            artifact["age_hours"] = round(age_seconds / 3600.0, 3)
            if age_seconds < -MAX_FUTURE_SKEW_SECONDS:
                reasons.append(f"future_{label}_run_at")
                artifact["status"] = "future"
            elif age_seconds > max_age_seconds:
                reasons.append(f"stale_{label}_snapshot")
                artifact["status"] = "stale"
            else:
                artifact["status"] = "fresh"
        freshness["artifacts"][label] = artifact
    return reasons, freshness


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def _float(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sanitize_text(value: str, workspace: str = "") -> str:
    safe = value
    workspace = workspace.rstrip("/")
    if workspace:
        safe = safe.replace(workspace, "[workspace]")
    safe = HOME_HERMES_RE.sub("[hermes-workspace]/", safe)
    safe = HOME_OPENCLAW_RE.sub("[legacy-openclaw-workspace]/", safe)
    safe = re.sub(r"/Users/[^\s\"'`|,}]+", "[redacted-local-path]", safe)
    safe = safe.replace("Mobile Documents", "[redacted-local-path]")
    safe = re.sub(r"(?i)discord(?:app)?\.com/api/webhooks/[^\s\"'`]+", "[redacted-webhook]", safe)
    safe = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[redacted-token]", safe)
    safe = re.sub(r"xox[baprs]-[A-Za-z0-9-]+", "[redacted-token]", safe)
    safe = re.sub(
        r"(?i)(api[_ -]?key|bot[_ -]?token)\s*[:=]\s*[^\s\"']+",
        r"\1=[redacted-token]",
        safe,
    )
    safe = re.sub(r"(?is)(private email body|mailbox-only).*", "[redacted-private-evidence]", safe)
    return safe


def sanitize_value(value: Any, workspace: str = "") -> Any:
    if isinstance(value, str):
        return sanitize_text(value, workspace)
    if isinstance(value, list):
        return [sanitize_value(item, workspace) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item, workspace) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_value(val, workspace) for key, val in value.items()}
    return value


def contains_forbidden(value: Any) -> bool:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return bool(FORBIDDEN_RE.search(text))


def iter_path_values(value: Any, *, key_hint: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if (key_text in TRAVELER_PATH_KEYS or key_text.endswith("_path")) and isinstance(item, str):
                paths.append(item)
            paths.extend(iter_path_values(item, key_hint=key_text))
    elif isinstance(value, list):
        for item in value:
            paths.extend(iter_path_values(item, key_hint=key_hint))
    elif isinstance(value, str) and key_hint.endswith("_path"):
        paths.append(value)
    return paths


def has_legacy_production_path(value: Any) -> bool:
    return any(LEGACY_PRODUCTION_PATH_RE.search(path) for path in iter_path_values(value))


def provider_errors(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in _list(discovery.get("provider_results")):
        row = _dict(item)
        error = str(row.get("error") or "").strip()
        error_kind = str(row.get("error_kind") or "").strip()
        status_code = _int(row.get("status_code") or row.get("http_status") or 0)
        if error or error_kind or status_code >= 400:
            errors.append(
                {
                    "provider": str(row.get("provider") or "unknown"),
                    "error_kind": error_kind or ("http_status" if status_code else "error"),
                    "status_code": status_code or None,
                    "error": sanitize_text(error)[:160],
                }
            )
    if _int(discovery.get("error_count")) > 0 and not errors:
        errors.append({"provider": "unknown", "error_kind": "error_count", "status_code": None, "error": ""})
    return errors


def report_candidate_count(report: dict[str, Any]) -> int:
    if "candidate_count" in report:
        return _int(report.get("candidate_count"))
    return _int(report.get("item_count"))


def classify_operation(
    *,
    scout: dict[str, Any],
    discovery: dict[str, Any],
    report: dict[str, Any],
    workspace: str,
    reward: dict[str, Any] | None = None,
    autotune: dict[str, Any] | None = None,
    as_of: str = "",
    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> dict[str, Any]:
    """Return a sanitized Traveler ops evaluation report."""

    reasons: list[str] = []
    fatal: list[str] = []
    artifact_snapshots = {"scout": scout, "discovery": discovery, "report": report}
    snapshots = {"scout": scout, "discovery": discovery, "report": report, "workspace": workspace}
    freshness_reasons, freshness = evaluate_freshness(artifact_snapshots, as_of=as_of, max_age_hours=max_age_hours)
    fatal.extend(freshness_reasons)
    if has_legacy_production_path(snapshots):
        fatal.append("legacy_openclaw_production_path")
    if contains_forbidden(sanitize_value(snapshots, workspace)):
        fatal.append("privacy_sanitization_failed")

    accepted = _int(discovery.get("accepted_count"))
    duplicate = _int(discovery.get("duplicate_count"))
    rejected = _int(discovery.get("rejected_count"))
    reviewed = _int(discovery.get("reviewed_count"))
    processed = _int(discovery.get("requests_processed"))
    report_candidates = report_candidate_count(report)
    requests_created = _int(scout.get("requests_created"))
    stale_pending = len(_list(scout.get("stale_pending_topics")))
    p_errors = provider_errors(discovery)
    any_processed = any(value > 0 for value in (accepted, duplicate, rejected, reviewed, processed, report_candidates, requests_created))

    status = "ok"
    if fatal:
        status = "failed"
        reasons.extend(fatal)
    elif accepted == 0 and p_errors:
        status = "failed"
        reasons.append("zero_accepted_with_provider_errors")
    elif accepted > 0 and p_errors:
        status = "degraded"
        reasons.append("partial_provider_error_with_accepted_candidates")
    elif accepted == 0 and duplicate > 0 and rejected == 0:
        status = "degraded"
        reasons.append("duplicates_only")
    elif stale_pending > 0 and accepted == 0:
        status = "degraded"
        reasons.append("stale_pending_blocked")
    elif accepted == 0 and any_processed:
        status = "degraded"
        reasons.append("zero_accepted_after_processing")
    elif not any_processed:
        status = "degraded"
        reasons.append("no_processing_observed")
    else:
        reasons.append("accepted_candidates_observed")

    path_values = iter_path_values(snapshots)
    sanitized_paths = [sanitize_text(path, workspace) for path in path_values]
    path_policy = {
        "workspace": sanitize_text(workspace, workspace),
        "hermes_workspace_observed": any("[hermes-workspace]" in sanitize_text(path, workspace) or ".hermes/workspace" in path for path in path_values + [workspace]),
        "legacy_openclaw_workspace_observed": any(LEGACY_PRODUCTION_PATH_RE.search(path) for path in path_values + [workspace]),
        "sanitized_path_count": len(path_values),
        "sanitized_path_markers": sorted({path for path in sanitized_paths if "[workspace]" in path or "[hermes-workspace]" in path or "[legacy-openclaw-workspace]" in path})[:8],
    }
    metrics = {
        "accepted_count": accepted,
        "duplicate_count": duplicate,
        "rejected_count": rejected,
        "reviewed_count": reviewed,
        "requests_processed": processed,
        "requests_created": requests_created,
        "report_candidate_count": report_candidates,
        "stale_pending_topic_count": stale_pending,
        "provider_error_count": len(p_errors),
        "providers_used": sanitize_value(discovery.get("providers_used", []), workspace),
    }
    reward_data = _dict(reward)
    approval = _dict(reward_data.get("approval"))
    diversity = _dict(reward_data.get("diversity"))
    eligible_samples = _int(reward_data.get("eligible_sample_count"))
    if reward_data:
        metrics.update(
            {
                "miner_approved_count": _int(approval.get("approved_count")),
                "miner_approval_denominator_count": _int(approval.get("denominator_count")),
                "miner_approval_rate_pct": _float(approval.get("approval_rate_pct")),
                "approval_censored_recent_count": _int(reward_data.get("censored_recent_unapproved_count")),
                "approved_diversity_score": _float(diversity.get("normalized_shannon")),
                "combined_reward_score": _float(reward_data.get("reward_score")),
            }
        )
    autotune_data = _dict(autotune)
    bounded_autotune = {
        "enabled": bool(reward_data),
        "eligible": bool(reward_data) and eligible_samples >= 5 and status != "failed",
        "minimum_eligible_samples": 5,
        "latest_status": sanitize_text(str(autotune_data.get("status") or "not_run"), workspace),
        "latest_reason": sanitize_text(str(autotune_data.get("reason") or ""), workspace),
        "allowed_targets": [
            "runtime/traveler-scout-topics.json:priority,max_candidates",
            "skills/jiphyeonjeon-traveler/SKILL.md:SKILLOPT:TRAVELER:SEARCH-SCOPE",
        ],
        "unrestricted_apply": False,
    }
    report_out = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": as_of or now_iso(),
        "skill": "jiphyeonjeon-traveler",
        "status": status,
        "automatic_apply": False,
        "requires_reviewer_gate": True,
        "reasons": reasons,
        "metrics": metrics,
        "optimization": {
            "reward_schema_version": sanitize_text(str(reward_data.get("schema_version") or ""), workspace),
            "primary_metric": "miner_exact_url_approval_rate",
            "primary_weight": 0.7,
            "secondary_metric": "approved_provider_domain_topic_shannon_diversity",
            "secondary_weight": 0.3,
            "eligible_sample_count": eligible_samples,
            "approval_window_days": _int(reward_data.get("approval_window_days")),
            "diversity_components": sanitize_value(diversity.get("components", {}), workspace),
        },
        "bounded_autotune": bounded_autotune,
        "path_policy": path_policy,
        "provider_errors": sanitize_value(p_errors, workspace),
        "freshness": freshness,
        "evidence": {
            "scout_run_at": sanitize_value(scout.get("run_at", ""), workspace),
            "discovery_run_at": sanitize_value(discovery.get("run_at", ""), workspace),
            "report_run_at": sanitize_value(report.get("run_at", ""), workspace),
            "miner_request_state": sanitize_value(report.get("miner_request_state", ""), workspace),
        },
    }
    if contains_forbidden(report_out):
        report_out["status"] = "failed"
        report_out["reasons"] = sorted({*reasons, "privacy_sanitization_failed"})
        report_out["provider_errors"] = []
    return report_out


def load_snapshot(path: Path, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"{label} missing: {path}"]
    try:
        value = read_json(path)
    except Exception as exc:  # noqa: BLE001 - CLI diagnostic should be concise.
        return None, [f"{label} malformed: {exc}"]
    if not isinstance(value, dict):
        return None, [f"{label} malformed: expected JSON object"]
    return value, []


def evaluate_files(args: argparse.Namespace) -> dict[str, Any]:
    errors: list[str] = []
    scout, scout_errors = load_snapshot(Path(args.scout), "scout")
    discovery, discovery_errors = load_snapshot(Path(args.discovery), "discovery")
    report, report_errors = load_snapshot(Path(args.report), "report")
    errors.extend(scout_errors + discovery_errors + report_errors)
    workspace = str(args.workspace or "").strip()
    if not workspace:
        errors.append("workspace missing")
    if errors:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": args.as_of or now_iso(),
            "skill": "jiphyeonjeon-traveler",
            "status": "failed",
            "automatic_apply": False,
            "requires_reviewer_gate": True,
            "reasons": ["missing_or_malformed_snapshot"],
            "errors": [sanitize_text(error, workspace) for error in errors],
            "metrics": {},
            "path_policy": {"workspace": sanitize_text(workspace), "legacy_openclaw_workspace_observed": False},
            "provider_errors": [],
        }
    reward: dict[str, Any] | None = None
    autotune: dict[str, Any] | None = None
    optional_warnings: list[str] = []
    for label, raw in (("reward", args.reward), ("autotune", args.autotune)):
        if not raw:
            continue
        value, value_errors = load_snapshot(Path(raw), label)
        if value_errors:
            optional_warnings.extend(value_errors)
        elif label == "reward":
            reward = value
        else:
            autotune = value
    result = classify_operation(
        scout=scout or {},
        discovery=discovery or {},
        report=report or {},
        reward=reward,
        autotune=autotune,
        workspace=workspace,
        as_of=args.as_of,
        max_age_hours=args.max_age_hours,
    )
    if optional_warnings:
        result["optimization_warnings"] = [sanitize_text(item, workspace) for item in optional_warnings]
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only SkillOpt Traveler operational evaluator")
    parser.add_argument("--scout", required=True)
    parser.add_argument("--discovery", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--reward", default="", help="Optional traveler-skillopt-reward.v1 artifact")
    parser.add_argument("--autotune", default="", help="Optional bounded auto-tune result artifact")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    report = evaluate_files(args)
    write_json(Path(args.out), report)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return STATUS_EXIT.get(str(report.get("status")), 2)


if __name__ == "__main__":
    raise SystemExit(main())
