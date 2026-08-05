"""Propose scoring-config changes from the outcome ledger, and apply them under a gate.

Tuning the confidence weights would be theatre. `decide_evidence` makes every
accept/reject call *before* it computes a score (blocked, fetch failed, no
metadata, topic terms with no keyword match), and the only consumers of the
score are a duplicate tie-break and the number printed in the daily report. So
`base_confidence` and friends change what the operator reads, not what the
traveler finds.

What actually changes outcomes is the curated portfolio: which static sources
get crawled when the providers rate-limit. Those are attributable, because every
ledger observation carries the URL.

The one proposal here is not statistical inference. A reviewer rejecting a
source candidate has already said "do not collect from here", so the tuner
carries that decision into the portfolio rather than re-deriving it. Inferring
quality from adoption rates was considered and dropped: the operator sees the
traveler's own score before deciding, so adoption partly measures trust in that
score, which is not a basis for automatically narrowing what the traveler may
find. The proposer therefore refuses far more often than it fires.

The legacy static-source drop proposal remains a separate explicit `--confirm`
step. A second, narrower sink may run unattended: `auto-apply` uses exact-URL
Miner approvals plus diversity evidence and can change only bounded scout-topic
allocation fields and the marker-delimited Traveler skill section. That path is
hash anchored, backed up, post-validated, rollback-safe, and lineage required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._shared import _read_jsonl_rows
from .config import _load_dotenv
from .traveler_outcomes import (
    EVENT_ADOPTED,
    EVENT_OBSERVED,
    EVENT_REVIEWED,
    default_ledger_path,
    skillopt_reward_report,
)

LOG = logging.getLogger(__name__)

SCHEMA_VERSION = "traveler-tuning.v1"
AUTO_SCHEMA_VERSION = "traveler-skillopt-auto-tuning.v1"
STATIC_PROVIDER = "static-technical-sources"
MARKER_START = "<!-- SKILLOPT:TRAVELER:SEARCH-SCOPE:START -->"
MARKER_END = "<!-- SKILLOPT:TRAVELER:SEARCH-SCOPE:END -->"
MIN_AUTO_ELIGIBLE_SAMPLE = 5

# A source appears in the ledger many times but is reviewed once, so "how many
# rejections" is the wrong question. The signal that matters is whether a human
# ever ruled on it. This only requires that the URL genuinely shows up as a
# discovery, so a config typo cannot be "dropped" on the strength of no data.
MIN_OBSERVATIONS_PER_SOURCE = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _privacy_clean(text: str) -> bool:
    lowered = text.lower()
    return all(token not in lowered for token in ("/users/", "discord.com/api/webhooks", "bot_token", "api_key", "sk-"))


def _is_suffix(path: Path, suffix: tuple[str, ...]) -> bool:
    parts = path.resolve().parts
    return len(parts) >= len(suffix) and tuple(parts[-len(suffix):]) == suffix


def _target_root(path: Path, marker: str) -> Path | None:
    parts = path.resolve().parts
    if marker not in parts:
        return None
    return Path(*parts[: parts.index(marker)])


def _append_lineage(lineage_path: Path | None, record: dict[str, Any]) -> None:
    if lineage_path is None:
        return
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    with lineage_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _last_applied_lineage(lineage_path: Path | None) -> dict[str, Any] | None:
    if lineage_path is None:
        return None
    rows = _read_jsonl_rows(lineage_path)
    return next((row for row in reversed(rows) if row.get("status") == "applied"), None)


def _reward_evidence_fingerprint(reward: dict[str, Any], topic_stats: dict[str, dict[str, int]]) -> str:
    stable = {
        "approval_window_days": reward.get("approval_window_days"),
        "eligible_sample_count": reward.get("eligible_sample_count"),
        "approved_url_count": reward.get("approved_url_count"),
        "orphan_miner_approval_count": reward.get("orphan_miner_approval_count"),
        "censored_recent_unapproved_count": reward.get("censored_recent_unapproved_count"),
        "approval": reward.get("approval"),
        "diversity": reward.get("diversity"),
        "topic_stats": topic_stats,
    }
    serialized = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _backup_path(path: Path, before_hash: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.{stamp}.{before_hash[:12]}.bak")


def summarize_ledger(ledger_path: Path) -> dict[str, dict[str, Any]]:
    """Per-URL outcome counts: observations, adoptions, and explicit verdicts."""
    rows = _read_jsonl_rows(ledger_path)
    adopted = {str(r.get("url_key") or "") for r in rows if r.get("event") == EVENT_ADOPTED}
    verdicts: dict[str, str] = {
        str(r.get("url_key") or ""): str(r.get("verdict") or "")
        for r in rows
        if r.get("event") == EVENT_REVIEWED
    }
    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event") != EVENT_OBSERVED:
            continue
        key = str(row.get("url_key") or "")
        url = str(row.get("url") or "")
        if not key or not url:
            continue
        entry = summary.setdefault(
            url,
            {"url_key": key, "provider": str(row.get("provider") or ""), "observations": 0, "adopted": 0, "approved": 0, "rejected": 0, "reviews": 0},
        )
        entry["observations"] += 1
        entry["adopted"] = int(key in adopted)
        verdict = verdicts.get(key, "")
        entry["approved"] = int(verdict == "approve")
        entry["rejected"] = int(verdict == "reject")
        entry["reviews"] = int(verdict in {"approve", "reject"})
    return summary


def propose_changes(ledger_path: Path, scoring: dict[str, Any]) -> dict[str, Any]:
    """Config proposals with their evidence, or refusals with the reason why.

    Only one proposal exists, and it is not an inference: a reviewer rejecting a
    source candidate has already said "do not collect from here", so the tuner
    carries that decision into the portfolio. Inferring quality from adoption
    rates was considered and dropped — adoption is confounded by the operator
    seeing the traveler's own score before deciding, so it cannot support an
    automatic narrowing of what the traveler is allowed to find.
    """
    summary = summarize_ledger(ledger_path)
    proposals: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    configured = {str(row[1]): row for row in scoring.get("static_sources", []) if isinstance(row, list) and len(row) == 4}

    for url, row in configured.items():
        stats = summary.get(url, {"observations": 0, "adopted": 0, "approved": 0, "rejected": 0, "reviews": 0})
        if stats["observations"] < MIN_OBSERVATIONS_PER_SOURCE:
            refusals.append({"target": url, "reason": "never_observed", "detail": "no ledger observation, so nothing links this config row to a real discovery"})
            continue
        if stats["reviews"] == 0:
            refusals.append({"target": url, "reason": "unreviewed", "detail": "no verdict recorded; an unreviewed source is not a rejected one"})
            continue
        if stats["approved"] or stats["adopted"]:
            refusals.append({"target": url, "reason": "approved_or_adopted", "detail": "the record supports keeping this source"})
            continue
        if stats["rejected"]:
            proposals.append({
                "action": "drop_static_source",
                "target": url,
                "title": str(row[0]),
                "source_type": str(row[2]),
                "evidence": dict(stats),
                "rationale": "a reviewer rejected this source candidate; dropping it carries out that decision",
            })

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "ledger_path": str(ledger_path),
        "sources_configured": len(configured),
        "sources_with_outcomes": sum(1 for url in configured if url in summary),
        "proposals": proposals,
        "refusals": refusals,
        "automatic_apply": False,
        "notes": [
            "confidence weights are deliberately not tuned: decide_evidence rejects before scoring, so those weights change the displayed number and duplicate tie-break, not what gets found",
            "a source with no verdict is unreviewed, not rejected; silence never justifies dropping it",
            "adoption rates are not used to propose changes: the operator sees the traveler's score before deciding, so adoption partly measures trust in that score",
            "proposals narrow what the traveler may find, so applying them stays a human step",
        ],
    }


def apply_proposals(
    *,
    scoring_path: Path,
    proposals: list[dict[str, Any]],
    baseline_sha256: str,
    lineage_path: Path | None = None,
) -> dict[str, Any]:
    """Apply accepted proposals to the scoring config, hash-anchored with a backup.

    Refuses if the config changed since `propose` read it, so a stale proposal
    cannot silently overwrite an edit made in between.
    """
    current = _sha256_file(scoring_path)
    if current != baseline_sha256:
        raise ValueError(f"scoring config changed since proposal was generated (expected {baseline_sha256[:12]}, found {current[:12]})")
    if not proposals:
        raise ValueError("no proposals to apply")

    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    drop_urls = {str(p["target"]) for p in proposals if p.get("action") == "drop_static_source"}

    kept = [row for row in scoring.get("static_sources", []) if not (isinstance(row, list) and len(row) == 4 and str(row[1]) in drop_urls)]
    if not kept:
        raise ValueError("refusing to empty the static portfolio; the traveler would lose its fallback when providers rate-limit")
    scoring["static_sources"] = kept

    backup = scoring_path.with_suffix(scoring_path.suffix + ".bak")
    shutil.copy2(scoring_path, backup)
    scoring_path.write_text(json.dumps(scoring, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    record = {
        "schema_version": SCHEMA_VERSION,
        "applied_at": _utc_now(),
        "scoring_path": str(scoring_path),
        "backup_path": str(backup),
        "before_sha256": baseline_sha256,
        "after_sha256": _sha256_file(scoring_path),
        "dropped_sources": sorted(drop_urls),
        "sources_remaining": len(kept),
    }
    if lineage_path is not None:
        lineage_path.parent.mkdir(parents=True, exist_ok=True)
        with lineage_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


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


def _topic_stats_from_ledger(ledger_path: Path, topics: dict[str, Any], *, as_of: str = "", approval_window_days: int = 7) -> dict[str, dict[str, int]]:
    rows = _read_jsonl_rows(ledger_path)
    topic_by_id = {str(row.get("id") or ""): str(row.get("id") or "") for row in topics.get("topics", []) if isinstance(row, dict)}
    topic_by_query = {str(row.get("query") or ""): str(row.get("id") or "") for row in topics.get("topics", []) if isinstance(row, dict)}
    observed: dict[str, dict[str, Any]] = {}
    handed: dict[str, datetime] = {}
    miner_labels: dict[str, str] = {}
    now = _parse_time(as_of) or datetime.now(timezone.utc)
    for row in rows:
        key = str(row.get("url_key") or "")
        if not key:
            continue
        if row.get("event") == EVENT_OBSERVED and key not in observed:
            observed[key] = row
        elif row.get("event") == "handed_off":
            parsed = _parse_time(row.get("handed_off_at"))
            if parsed is not None:
                handed[key] = parsed
        elif row.get("event") in {"miner_approved", "miner_approval_revoked"}:
            miner_labels[key] = str(row.get("event"))
    approved = {key for key, label in miner_labels.items() if label == "miner_approved" and key in handed}
    stats: dict[str, dict[str, int]] = {}
    for key, row in observed.items():
        topic_id = topic_by_id.get(str(row.get("topic_id") or ""), "") or topic_by_query.get(str(row.get("query") or ""), "")
        if not topic_id or key not in handed:
            continue
        is_approved = key in approved
        is_matured = (now - handed[key]).total_seconds() / 86400 >= approval_window_days
        if not is_approved and not is_matured:
            continue
        entry = stats.setdefault(topic_id, {"eligible": 0, "approved": 0})
        entry["eligible"] += 1
        entry["approved"] += int(is_approved)
    return stats


def _update_topics(topics: dict[str, Any], topic_stats: dict[str, dict[str, int]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    changed: list[dict[str, Any]] = []
    out = json.loads(json.dumps(topics))
    for row in out.get("topics", []) if isinstance(out.get("topics"), list) else []:
        if not isinstance(row, dict):
            continue
        stats = topic_stats.get(str(row.get("id") or ""), {"eligible": 0, "approved": 0})
        eligible = int(stats.get("eligible") or 0)
        approved = int(stats.get("approved") or 0)
        if eligible < 2:
            continue
        approval_rate = approved / eligible if eligible else 0.0
        before = {"id": row.get("id"), "priority": row.get("priority"), "max_candidates": row.get("max_candidates")}
        current = int(row.get("max_candidates") or 1)
        if approval_rate >= 0.6 and (current < 8 or row.get("priority") != "high"):
            row["priority"] = "high"
            row["max_candidates"] = max(1, min(8, current + 1))
        elif approval_rate <= 0.2 and current > 1:
            row["max_candidates"] = max(1, current - 1)
        after = {"id": row.get("id"), "priority": row.get("priority"), "max_candidates": row.get("max_candidates")}
        if before != after:
            changed.append({"before": before, "after": after, "eligible": eligible, "approved": approved, "approval_rate": round(approval_rate, 3)})
    if len(out.get("topics", [])) != len(topics.get("topics", [])):
        raise ValueError("search topic deletion detected")
    for row in out.get("topics", []):
        if isinstance(row, dict):
            row["max_candidates"] = max(1, min(8, int(row.get("max_candidates") or 1)))
    return out, changed


def _allocation_diversity(topics: dict[str, Any]) -> float:
    values: list[str] = []
    for row in topics.get("topics", []) if isinstance(topics.get("topics"), list) else []:
        if not isinstance(row, dict):
            continue
        values.extend([str(row.get("id") or "unknown")] * max(1, int(row.get("max_candidates") or 1)))
    if len(values) <= 1:
        return 0.0
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    from math import log2

    entropy = -sum((count / len(values)) * log2(count / len(values)) for count in counts.values())
    return entropy / log2(len(counts)) if len(counts) > 1 else 0.0


def _replace_marker_section(text: str, replacement: str) -> str:
    start = text.find(MARKER_START)
    end = text.find(MARKER_END)
    if start < 0 or end < 0 or end < start:
        raise ValueError("skillopt_marker_missing")
    end += len(MARKER_END)
    return text[:start] + MARKER_START + "\n" + replacement.strip() + "\n" + MARKER_END + text[end:]


def auto_apply_bounded(
    *,
    ledger_path: Path,
    topics_path: Path,
    skill_path: Path,
    topics_baseline_sha256: str | None,
    skill_baseline_sha256: str | None,
    lineage_path: Path | None,
    as_of: str = "",
    approval_window_days: int = 7,
    post_validate: Any | None = None,
) -> dict[str, Any]:
    reward = skillopt_reward_report(ledger_path, as_of=as_of, approval_window_days=approval_window_days)
    if int(reward.get("eligible_sample_count") or 0) < MIN_AUTO_ELIGIBLE_SAMPLE:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "no_op", "reason": "insufficient_eligible_sample", "reward": reward}
    if not _is_suffix(topics_path, ("runtime", "traveler-scout-topics.json")) or not _is_suffix(skill_path, ("skills", "jiphyeonjeon-traveler", "SKILL.md")):
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "invalid_target_path"}
    topics_root = _target_root(topics_path, "runtime")
    skill_root = _target_root(skill_path, "skills")
    try:
        if topics_root is None or skill_root is None or os.path.commonpath([str(topics_root), str(skill_root)]) != str(topics_root):
            return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "target_root_mismatch"}
    except ValueError:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "target_root_mismatch"}

    current_topics_hash = _sha256_file(topics_path)
    current_skill_hash = _sha256_file(skill_path)
    if topics_baseline_sha256 and topics_baseline_sha256 != current_topics_hash:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "stale_topics_baseline"}
    if skill_baseline_sha256 and skill_baseline_sha256 != current_skill_hash:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "stale_skill_baseline"}

    topics = json.loads(topics_path.read_text(encoding="utf-8"))
    skill_text = skill_path.read_text(encoding="utf-8")
    topic_stats = _topic_stats_from_ledger(ledger_path, topics, as_of=as_of, approval_window_days=approval_window_days)
    evidence_fingerprint = _reward_evidence_fingerprint(reward, topic_stats)
    previous = _last_applied_lineage(lineage_path)
    if (
        previous
        and previous.get("evidence_fingerprint") == evidence_fingerprint
        and previous.get("after", {}).get("topics_sha256") == current_topics_hash
        and previous.get("after", {}).get("skill_sha256") == current_skill_hash
    ):
        return {
            "schema_version": AUTO_SCHEMA_VERSION,
            "status": "no_op",
            "reason": "reward_evidence_already_applied",
            "evidence_fingerprint": evidence_fingerprint,
            "reward": reward,
        }

    try:
        updated_topics, topic_changes = _update_topics(
            topics,
            topic_stats,
        )
        if not topic_changes:
            return {"schema_version": AUTO_SCHEMA_VERSION, "status": "no_op", "reason": "no_eligible_topic_changes", "reward": reward}
        marker = (
            "SkillOpt Traveler search-scope contract:\n\n"
            "- Primary reward is the latest Miner/Claw exact-URL approval within the 7-day censor window, weighted 70%.\n"
            "- Exploration reward is approved-set Shannon diversity across provider, domain, and topic buckets, weighted 30%.\n"
            "- Minimum eligible sample size is 5; below that, do not apply changes.\n"
            "- Automatic tuning may edit only `runtime/traveler-scout-topics.json` fields `priority` and `max_candidates` and this marker-bounded section.\n"
            "- Automatic tuning must not edit scoring thresholds, provider code, services, cron, queues, Discord state, or PaperWiki artifacts.\n"
            "- Every applied change must preserve hash, backup, rollback, and append-only lineage evidence.\n\n"
            f"Last bounded auto-apply: {_utc_now()}\n"
            f"Reward schema: {reward['schema_version']}\n"
            f"Eligible sample: {reward['eligible_sample_count']}\n"
            f"Approval rate: {reward['approval']['approval_rate_pct']}%\n"
            f"Diversity: {reward['diversity']['normalized_shannon']}"
        )
        updated_skill_text = _replace_marker_section(skill_text, marker)
    except ValueError as exc:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": str(exc)}

    if not topic_changes and updated_skill_text == skill_text:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "no_op", "reason": "no_eligible_target_changes", "reward": reward}
    if lineage_path is None:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "lineage_required"}
    current_allocation_diversity = _allocation_diversity(topics)
    projected_allocation_diversity = _allocation_diversity(updated_topics)
    if projected_allocation_diversity + 0.03 < current_allocation_diversity:
        return {
            "schema_version": AUTO_SCHEMA_VERSION,
            "status": "blocked",
            "reason": "projected_diversity_regression",
            "current_allocation_diversity": round(current_allocation_diversity, 4),
            "projected_allocation_diversity": round(projected_allocation_diversity, 4),
        }

    serialized_topics = json.dumps(updated_topics, ensure_ascii=False, indent=2) + "\n"
    if not _privacy_clean(serialized_topics + updated_skill_text):
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "privacy_check_failed"}

    # Rehash immediately before writing, so a concurrent edit between planning
    # and apply is still caught.
    prewrite_topics_hash = _sha256_file(topics_path)
    prewrite_skill_hash = _sha256_file(skill_path)
    if prewrite_topics_hash != current_topics_hash:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "topics_changed_during_planning"}
    if prewrite_skill_hash != current_skill_hash:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "skill_changed_during_planning"}
    if topics_baseline_sha256 and topics_baseline_sha256 != prewrite_topics_hash:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "stale_topics_baseline"}
    if skill_baseline_sha256 and skill_baseline_sha256 != prewrite_skill_hash:
        return {"schema_version": AUTO_SCHEMA_VERSION, "status": "blocked", "reason": "stale_skill_baseline"}

    topics_backup = _backup_path(topics_path, prewrite_topics_hash)
    skill_backup = _backup_path(skill_path, prewrite_skill_hash)
    shutil.copy2(topics_path, topics_backup)
    shutil.copy2(skill_path, skill_backup)
    try:
        _atomic_write(topics_path, serialized_topics)
        _atomic_write(skill_path, updated_skill_text)
        # Post-validation: exact topic set, bounds, marker containment, privacy.
        reread_topics = json.loads(topics_path.read_text(encoding="utf-8"))
        if {row.get("id") for row in reread_topics.get("topics", [])} != {row.get("id") for row in topics.get("topics", [])}:
            raise ValueError("search_topic_set_changed")
        for row in reread_topics.get("topics", []):
            max_candidates = int(row.get("max_candidates") or 0)
            if max_candidates < 1 or max_candidates > 8:
                raise ValueError("max_candidates_out_of_bounds")
        if MARKER_START not in skill_path.read_text(encoding="utf-8") or MARKER_END not in skill_path.read_text(encoding="utf-8"):
            raise ValueError("skillopt_marker_missing_after_write")
        if post_validate is not None:
            post_validate(topics_path, skill_path)
    except Exception:  # noqa: BLE001 - any post-write failure must rollback both targets.
        shutil.copy2(topics_backup, topics_path)
        shutil.copy2(skill_backup, skill_path)
        rollback = {
            "schema_version": AUTO_SCHEMA_VERSION,
            "status": "rolled_back",
            "reason": str(sys.exc_info()[1]),
            "rolled_back_at": _utc_now(),
            "topics_path": str(topics_path),
            "skill_path": str(skill_path),
            "topics_backup_path": str(topics_backup),
            "skill_backup_path": str(skill_backup),
        }
        _append_lineage(lineage_path, rollback)
        return rollback

    record = {
        "schema_version": AUTO_SCHEMA_VERSION,
        "status": "applied",
        "applied_at": _utc_now(),
        "topics_path": str(topics_path),
        "skill_path": str(skill_path),
        "topics_backup_path": str(topics_backup),
        "skill_backup_path": str(skill_backup),
        "before": {"topics_sha256": current_topics_hash, "skill_sha256": current_skill_hash},
        "after": {"topics_sha256": _sha256_file(topics_path), "skill_sha256": _sha256_file(skill_path)},
        "topic_changes": topic_changes,
        "evidence_fingerprint": evidence_fingerprint,
        "reward": reward,
    }
    _append_lineage(lineage_path, record)
    return record


def _default_scoring_path() -> Path:
    from .traveler_evidence import _scoring_path

    return _scoring_path()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="discord-openclaw-traveler-tune",
        description="Propose Traveler scoring-config changes from recorded outcomes; apply only with --confirm.",
    )
    parser.add_argument("--ledger", type=Path, default=None)
    parser.add_argument("--scoring", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("propose", help="Print proposals and refusals. Changes nothing.")
    apply_cmd = sub.add_parser("apply", help="Apply proposals. Requires --confirm.")
    apply_cmd.add_argument("--confirm", action="store_true", help="Required. Without it this is a dry run.")
    apply_cmd.add_argument("--lineage", type=Path, default=None, help="Append an applied-change record here.")
    auto_cmd = sub.add_parser("auto-apply", help="Bounded automatic tuning for Traveler SkillOpt targets.")
    auto_cmd.add_argument("--topics", type=Path, required=True)
    auto_cmd.add_argument("--skill", type=Path, required=True)
    auto_cmd.add_argument("--topics-baseline-sha256", required=True)
    auto_cmd.add_argument("--skill-baseline-sha256", required=True)
    auto_cmd.add_argument("--lineage", type=Path, default=None)
    auto_cmd.add_argument("--as-of", default="")
    auto_cmd.add_argument("--approval-window-days", type=int, default=7)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    _load_dotenv(Path.cwd() / ".env")
    args = build_parser().parse_args(argv)
    ledger = (args.ledger or default_ledger_path()).expanduser()
    if args.command == "auto-apply":
        record = auto_apply_bounded(
            ledger_path=ledger,
            topics_path=args.topics.expanduser(),
            skill_path=args.skill.expanduser(),
            topics_baseline_sha256=args.topics_baseline_sha256,
            skill_baseline_sha256=args.skill_baseline_sha256,
            lineage_path=args.lineage.expanduser() if args.lineage else None,
            as_of=args.as_of,
            approval_window_days=args.approval_window_days,
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 2 if record.get("status") == "blocked" else 0

    scoring_path = (args.scoring or _default_scoring_path()).expanduser()
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    report = propose_changes(ledger, scoring)

    if args.command == "propose":
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if not report["proposals"]:
        print("no proposals met the evidence guards; nothing to apply", file=sys.stderr)
        return 1
    if not args.confirm:
        print(json.dumps({**report, "dry_run": True, "hint": "re-run with --confirm to apply"}, ensure_ascii=False, indent=2))
        return 0

    record = apply_proposals(
        scoring_path=scoring_path,
        proposals=report["proposals"],
        baseline_sha256=_sha256_file(scoring_path),
        lineage_path=args.lineage.expanduser() if args.lineage else None,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
