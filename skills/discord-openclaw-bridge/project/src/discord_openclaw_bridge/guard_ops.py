"""Operational issue digest for 집현전-경비원.

The guard agent is intentionally lightweight: it reads already-produced local
artifacts and turns silent drift into operator-facing issue records.  It does
not mutate review state, approve content, or post to Discord by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._shared import _parse_utc, _severity_status
from .config import _load_dotenv
from .miner import _append_jsonl_unlocked, locked_jsonl_paths, read_jsonl, sanitize_url
from .review import latest_decisions

DEFAULT_WORKSPACE = Path.home() / ".openclaw" / "workspace"
DEFAULT_STATUS_PATH = DEFAULT_WORKSPACE / "state" / "miner-seeds-last-status.json"
DEFAULT_TRAVELER_REPORT_STATUS_PATH = DEFAULT_WORKSPACE / "state" / "traveler-collection-report-last-status.json"
DEFAULT_MINER_INTAKE_PATH = DEFAULT_WORKSPACE / "intake" / "jiphyeonjeon-miner" / "links.jsonl"
DEFAULT_REVIEW_QUEUE_PATH = (
    DEFAULT_WORKSPACE / "review" / "jiphyeonjeon-claw" / "link-review-queue.jsonl"
)
DEFAULT_DECISIONS_PATH = (
    DEFAULT_WORKSPACE / "review" / "jiphyeonjeon-claw" / "link-review-decisions.jsonl"
)
DEFAULT_ISSUE_QUEUE_PATH = (
    DEFAULT_WORKSPACE / "review" / "jiphyeonjeon-guard" / "ops-issue-queue.jsonl"
)

DEFAULT_MAX_STATUS_AGE_HOURS = 26.0
DEFAULT_MAX_PENDING_AGE_DAYS = 7.0
DEFAULT_MAX_PENDING_COUNT = 20



def _issue(
    *,
    severity: str,
    category: str,
    signal: str,
    message: str,
    evidence: str,
    recommended_action: str,
) -> dict[str, str]:
    issue_key = json.dumps(
        {
            "category": category,
            "signal": signal,
            "evidence": evidence,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "issue_id": "guard_" + hashlib.sha256(issue_key.encode("utf-8")).hexdigest()[:16],
        "severity": severity,
        "category": category,
        "signal": signal,
        "message": message,
        "evidence": evidence,
        "recommended_action": recommended_action,
    }



def _url_hash(url: str) -> str:
    safe_url = sanitize_url(url)
    if not safe_url:
        return ""
    return hashlib.sha256(safe_url.encode("utf-8")).hexdigest()[:16]



def _guard_config_from_env(env: dict[str, str]) -> dict[str, bool | str]:
    guard_token_set = bool(env.get("DISCORD_GUARD_BOT_TOKEN", "").strip())
    bridge_token_set = bool(env.get("DISCORD_BOT_TOKEN", "").strip())
    ops_channel_set = bool(env.get("DISCORD_OPS_REPORT_CHANNEL_ID", "").strip())
    # Compatibility/fail-safe rationale:
    # post_miner_seeds_report._resolve_bot_token() intentionally keeps the
    # legacy bridge bot token as a rollout safety net so operations reporting
    # does not go dark while the dedicated Guard application is provisioned.
    # The digest mirrors that contract without exposing token values; it emits
    # a warning issue so operators can complete the migration to the dedicated
    # Guard identity.  Covered by test_guard_config_reports_bridge_fallback...
    if guard_token_set:
        token_source = "guard"
    elif bridge_token_set:
        token_source = "bridge-fallback"
    else:
        token_source = "missing"
    return {
        "guard_token_set": guard_token_set,
        "bridge_token_set": bridge_token_set,
        "ops_report_channel_set": ops_channel_set,
        "token_source": token_source,
    }


def _pending_review_items(
    queue_rows: list[dict[str, Any]],
    decisions_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in decisions_rows:
        decision = str(row.get("decision") or "")
        intake_id = str(row.get("intake_id") or "")
        if intake_id and decision in {"approve", "reject", "hold"}:
            latest[intake_id] = row
    return [
        row
        for row in queue_rows
        if str(row.get("intake_id") or "") and str(row.get("intake_id") or "") not in latest
    ]


def _traveler_miner_handoff(
    *,
    traveler_report_status: dict[str, Any] | None,
    traveler_status_path: str,
    miner_intake_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Summarize the Traveler report -> Miner intake handoff without mutating state."""

    issues: list[dict[str, str]] = []
    requested_hashes = [
        str(value)
        for value in (traveler_report_status or {}).get("miner_request_url_hashes", [])
        if str(value)
    ]
    miner_message_id = str((traveler_report_status or {}).get("miner_message_id") or "")
    matched_intake_ids: list[str] = []
    covered_hashes: set[str] = set()
    duplicate_or_preexisting = 0
    for row in miner_intake_rows:
        discord = row.get("discord") if isinstance(row.get("discord"), dict) else {}
        url_digest = _url_hash(str(row.get("url") or ""))
        if miner_message_id and str(discord.get("message_id") or "") == miner_message_id and url_digest in requested_hashes:
            matched_intake_ids.append(str(row.get("intake_id") or ""))
            covered_hashes.add(url_digest)
        elif url_digest in requested_hashes:
            duplicate_or_preexisting += 1
            covered_hashes.add(url_digest)

    missing_count = max(0, len(set(requested_hashes)) - len(covered_hashes))
    status = "not_requested"
    if traveler_report_status is None:
        status = "missing_status"
        issues.append(
            _issue(
                severity="warning",
                category="traveler_miner_handoff",
                signal="traveler_report_status_missing",
                message="Traveler collection report status artifact is missing",
                evidence=traveler_status_path,
                recommended_action="Confirm the Traveler daily collection report cron has run and wrote its status artifact.",
            )
        )
    elif requested_hashes and not miner_message_id:
        status = "request_missing"
        issues.append(
            _issue(
                severity="warning",
                category="traveler_miner_handoff",
                signal="miner_request_missing",
                message="Traveler report selected Miner request URLs but no Miner request message id was recorded",
                evidence=traveler_status_path,
                recommended_action="Check Traveler report posting logs and DISCORD_MINER_CHANNEL_ID/DISCORD_MINER_CLIENT_ID configuration.",
            )
        )
    elif requested_hashes and duplicate_or_preexisting and not matched_intake_ids and not missing_count:
        status = "duplicate_preexisting"
        issues.append(
            _issue(
                severity="warning",
                category="traveler_miner_handoff",
                signal="miner_request_duplicate_preexisting",
                message="Traveler-requested URL(s) were already present before this Miner request; current message processing was not confirmed",
                evidence=traveler_status_path,
                recommended_action="Review whether the Miner bot processed the current request or skipped duplicates intentionally.",
            )
        )
    elif requested_hashes and missing_count:
        status = "unconfirmed"
        issues.append(
            _issue(
                severity="warning",
                category="traveler_miner_handoff",
                signal="miner_intake_unconfirmed",
                message=f"Miner intake has not confirmed {missing_count} Traveler-requested URL(s)",
                evidence=traveler_status_path,
                recommended_action="Check the Miner bot service, message-content intent, Traveler bot id gate, and Miner intake JSONL.",
            )
        )
    elif requested_hashes:
        status = "ok"

    return (
        {
            "status": status,
            "traveler_status_path": traveler_status_path,
            "miner_message_id": miner_message_id,
            "requested_url_hashes": requested_hashes,
            "matched_intake_ids": [item for item in matched_intake_ids if item],
            "missing_count": missing_count,
            "duplicate_or_preexisting_count": duplicate_or_preexisting,
        },
        issues,
    )


def build_ops_digest(
    *,
    status: dict[str, Any] | None,
    status_path: str,
    review_queue_path: str = str(DEFAULT_REVIEW_QUEUE_PATH),
    queue_rows: list[dict[str, Any]] | None = None,
    decisions_rows: list[dict[str, Any]] | None = None,
    traveler_report_status: dict[str, Any] | None = None,
    traveler_status_path: str = str(DEFAULT_TRAVELER_REPORT_STATUS_PATH),
    miner_intake_rows: list[dict[str, Any]] | None = None,
    enable_traveler_handoff: bool = False,
    guard_config: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_status_age_hours: float = DEFAULT_MAX_STATUS_AGE_HOURS,
    max_pending_age_days: float = DEFAULT_MAX_PENDING_AGE_DAYS,
    max_pending_count: int = DEFAULT_MAX_PENDING_COUNT,
) -> dict[str, Any]:
    """Return a stable issue digest from guard-owned operations artifacts."""

    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    issues: list[dict[str, str]] = []
    handoffs: dict[str, Any] = {}

    if guard_config is not None:
        token_source = str(guard_config.get("token_source") or "missing")
        if token_source == "missing":
            issues.append(
                _issue(
                    severity="error",
                    category="guard_config",
                    signal="bot_token_missing",
                    message="neither DISCORD_GUARD_BOT_TOKEN nor DISCORD_BOT_TOKEN is configured",
                    evidence="discord-openclaw-bridge .env",
                    recommended_action="Set DISCORD_GUARD_BOT_TOKEN for the dedicated guard identity; use DISCORD_BOT_TOKEN only for the documented rollout compatibility path.",
                )
            )
        elif token_source == "bridge-fallback":
            issues.append(
                _issue(
                    severity="warning",
                    category="guard_config",
                    signal="guard_token_missing",
                    message="DISCORD_GUARD_BOT_TOKEN is missing; guard reports will use the bridge fallback identity",
                    evidence="discord-openclaw-bridge .env",
                    recommended_action="Provision the dedicated Jiphyeonjeon-Guard bot token and invite it to the operations forum.",
                )
            )
        if not bool(guard_config.get("ops_report_channel_set")):
            issues.append(
                _issue(
                    severity="warning",
                    category="guard_config",
                    signal="ops_report_channel_default",
                    message="DISCORD_OPS_REPORT_CHANNEL_ID is not configured; the guard report will use the built-in default channel",
                    evidence="discord-openclaw-bridge .env",
                    recommended_action="Set DISCORD_OPS_REPORT_CHANNEL_ID explicitly to make the operations forum target auditable.",
                )
            )

    if status is None:
        issues.append(
            _issue(
                severity="error",
                category="guard_status",
                signal="missing_status",
                message="miner-seeds last status artifact is missing",
                evidence=status_path,
                recommended_action="Confirm the miner-seeds cron is installed and has run at least once.",
            )
        )
    else:
        run_at = _parse_utc(status.get("run_at"))
        if run_at is None:
            issues.append(
                _issue(
                    severity="warning",
                    category="guard_status",
                    signal="invalid_run_at",
                    message="miner-seeds status has no parseable run_at timestamp",
                    evidence=status_path,
                    recommended_action="Inspect the status writer and recent miner-seeds logs.",
                )
            )
        else:
            age_hours = (current_time - run_at).total_seconds() / 3600
            if age_hours > max_status_age_hours:
                issues.append(
                    _issue(
                        severity="warning",
                        category="scheduler",
                        signal="stale_status",
                        message=f"last miner-seeds run is stale ({age_hours:.1f}h)",
                        evidence=status_path,
                        recommended_action="Check cron entry, miner-seeds log, and host scheduler state.",
                    )
                )

        seeds_with_errors = int(status.get("seeds_with_errors") or 0)
        if seeds_with_errors:
            issues.append(
                _issue(
                    severity="error",
                    category="seed_health",
                    signal="seed_errors",
                    message=f"{seeds_with_errors} seed(s) reported errors",
                    evidence=status_path,
                    recommended_action="Review per-seed error details for selector drift, rate limits, or network failures.",
                )
            )

        accepted = int(status.get("total_accepted") or 0)
        seeds_total = int(status.get("seeds_total") or 0)
        skipped = int(status.get("seeds_skipped_cooldown") or 0)
        if accepted == 0 and seeds_total > 0 and skipped != seeds_total and seeds_with_errors == 0:
            issues.append(
                _issue(
                    severity="warning",
                    category="seed_health",
                    signal="zero_accepted",
                    message="miner-seeds run accepted no records outside a full cooldown state",
                    evidence=status_path,
                    recommended_action="Check source filters, duplicate saturation, and collection expansion quality.",
                )
            )

    queue_rows = queue_rows or []
    decisions_rows = decisions_rows or []
    if enable_traveler_handoff:
        handoff, handoff_issues = _traveler_miner_handoff(
            traveler_report_status=traveler_report_status,
            traveler_status_path=traveler_status_path,
            miner_intake_rows=miner_intake_rows or [],
        )
        handoffs["traveler_to_miner"] = handoff
        issues.extend(handoff_issues)
    pending = _pending_review_items(queue_rows, decisions_rows)
    if len(pending) > max_pending_count:
        issues.append(
            _issue(
                severity="warning",
                category="review_backlog",
                signal="pending_count_high",
                message=f"review queue has {len(pending)} pending item(s)",
                evidence=review_queue_path,
                recommended_action="Schedule Jiphyeonjeon-Claw review or raise the backlog threshold deliberately.",
            )
        )

    oldest_pending_at: str | None = None
    oldest_age_days = 0.0
    for row in pending:
        created_at = _parse_utc(row.get("created_at"))
        if created_at is None:
            continue
        age_days = (current_time - created_at).total_seconds() / 86400
        if oldest_pending_at is None or age_days > oldest_age_days:
            oldest_pending_at = created_at.isoformat().replace("+00:00", "Z")
            oldest_age_days = age_days
    if oldest_pending_at and oldest_age_days > max_pending_age_days:
        issues.append(
            _issue(
                severity="warning",
                category="review_backlog",
                signal="pending_age_high",
                message=f"oldest pending review item is {oldest_age_days:.1f}d old",
                evidence=review_queue_path,
                recommended_action="Review or hold stale pending items so downstream inclusion remains intentional.",
            )
        )

    digest = {
        "agent_id": "jiphyeonjeon-guard",
        "generated_at": current_time.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "health_status": _severity_status(issues),
        "summary": {
            "issue_count": len(issues),
            "pending_review_count": len(pending),
            "oldest_pending_at": oldest_pending_at,
        },
        "handoffs": handoffs,
        "issues": issues,
    }
    if status is not None:
        digest["miner_seeds"] = {
            "run_at": status.get("run_at"),
            "seeds_total": status.get("seeds_total", 0),
            "seeds_with_errors": status.get("seeds_with_errors", 0),
            "total_accepted": status.get("total_accepted", 0),
            "total_duplicate": status.get("total_duplicate", 0),
            "total_rejected": status.get("total_rejected", 0),
        }
    if guard_config is not None:
        digest["guard_config"] = {
            "guard_token_set": bool(guard_config.get("guard_token_set")),
            "bridge_token_set": bool(guard_config.get("bridge_token_set")),
            "ops_report_channel_set": bool(guard_config.get("ops_report_channel_set")),
            "token_source": guard_config.get("token_source", "missing"),
        }
    return digest


def write_issue_queue(path: Path, digest: dict[str, Any]) -> int:
    """Append newly observed guard issues to an append-only JSONL queue.

    The queue is intentionally separate from Jiphyeonjeon-Claw content review.
    Existing ``issue_id`` values are not rewritten; future acknowledge/resolve
    workflows can append separate audit events without changing this source.
    """

    issues = [issue for issue in digest.get("issues", []) if isinstance(issue, dict)]
    if not issues:
        return 0
    appended = 0
    recorded_at = str(digest.get("generated_at") or datetime.now(timezone.utc).isoformat())
    with locked_jsonl_paths(path):
        existing_ids = {
            str(row.get("issue_id") or "")
            for row in read_jsonl(path)
            if isinstance(row, dict) and row.get("issue_id")
        }
        for issue in issues:
            issue_id = str(issue.get("issue_id") or "")
            if not issue_id or issue_id in existing_ids:
                continue
            _append_jsonl_unlocked(
                path,
                {
                    "issue_id": issue_id,
                    "agent_id": digest.get("agent_id", "jiphyeonjeon-guard"),
                    "status": "open",
                    "first_seen": recorded_at,
                    "last_seen": recorded_at,
                    "source": "guard_ops_digest",
                    "issue": issue,
                },
            )
            existing_ids.add(issue_id)
            appended += 1
    return appended


def _read_status(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"status payload is not an object: {path}")
    return raw


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-guard-ops-digest",
        description="Summarise Guard-owned operations issue signals.",
    )
    parser.add_argument(
        "--status-path",
        type=Path,
        default=Path(os.getenv("MINER_SEEDS_STATUS_PATH", str(DEFAULT_STATUS_PATH))),
    )
    parser.add_argument(
        "--traveler-report-status-path",
        type=Path,
        default=Path(os.getenv("JIPHYEONJEON_TRAVELER_REPORT_STATUS_PATH", str(DEFAULT_TRAVELER_REPORT_STATUS_PATH))),
    )
    parser.add_argument(
        "--miner-intake-path",
        type=Path,
        default=Path(os.getenv("JIPHYEONJEON_MINER_INTAKE_PATH", str(DEFAULT_MINER_INTAKE_PATH))),
    )
    parser.add_argument(
        "--review-queue-path",
        type=Path,
        default=Path(os.getenv("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", str(DEFAULT_REVIEW_QUEUE_PATH))),
    )
    parser.add_argument(
        "--decisions-path",
        type=Path,
        default=Path(os.getenv("JIPHYEONJEON_MINER_DECISIONS_PATH", str(DEFAULT_DECISIONS_PATH))),
    )
    parser.add_argument(
        "--issue-queue-path",
        type=Path,
        default=Path(os.getenv("JIPHYEONJEON_GUARD_ISSUE_QUEUE_PATH", str(DEFAULT_ISSUE_QUEUE_PATH))),
    )
    parser.add_argument(
        "--env-path",
        type=Path,
        default=Path.cwd() / ".env",
        help="Optional dotenv path used only for sanitized guard config presence checks.",
    )
    parser.add_argument("--max-status-age-hours", type=float, default=DEFAULT_MAX_STATUS_AGE_HOURS)
    parser.add_argument("--max-pending-age-days", type=float, default=DEFAULT_MAX_PENDING_AGE_DAYS)
    parser.add_argument("--max-pending-count", type=int, default=DEFAULT_MAX_PENDING_COUNT)
    parser.add_argument("--write-issue-queue", action="store_true", help="append newly observed issues to guard JSONL queue")
    parser.add_argument("--fail-on-error", action="store_true", help="exit non-zero when digest health is error")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        status_path = args.status_path.expanduser()
        traveler_status_path = args.traveler_report_status_path.expanduser()
        miner_intake_path = args.miner_intake_path.expanduser()
        queue_path = args.review_queue_path.expanduser()
        decisions_path = args.decisions_path.expanduser()
        _load_dotenv(args.env_path.expanduser())
        digest = build_ops_digest(
            status=_read_status(status_path),
            status_path=str(status_path),
            traveler_report_status=_read_status(traveler_status_path),
            traveler_status_path=str(traveler_status_path),
            miner_intake_rows=read_jsonl(miner_intake_path),
            enable_traveler_handoff=True,
            review_queue_path=str(queue_path),
            queue_rows=read_jsonl(queue_path),
            decisions_rows=list(latest_decisions(decisions_path).values()),
            guard_config=_guard_config_from_env(os.environ),
            max_status_age_hours=args.max_status_age_hours,
            max_pending_age_days=args.max_pending_age_days,
            max_pending_count=args.max_pending_count,
        )
        if args.write_issue_queue:
            digest["summary"]["issue_queue_appended"] = write_issue_queue(
                args.issue_queue_path.expanduser(),
                digest,
            )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        digest = {
            "agent_id": "jiphyeonjeon-guard",
            "health_status": "error",
            "summary": {"issue_count": 1},
            "issues": [
                _issue(
                    severity="error",
                    category="artifact_integrity",
                    signal="read_failed",
                    message=str(exc),
                    evidence="guard ops artifact read",
                    recommended_action="Inspect JSON artifact syntax and filesystem permissions.",
                )
            ],
        }
    print(json.dumps(digest, ensure_ascii=False, indent=2, sort_keys=True))
    if args.fail_on_error and digest.get("health_status") == "error":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
