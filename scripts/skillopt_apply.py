#!/usr/bin/env python3
"""SkillOpt Phase 4 controlled proposal apply.

This script is intentionally stdlib-only. It selects one safe SkillOpt proposal,
previews it without mutation, and applies it only with explicit reviewer and
critic approval plus eval evidence. Proposal generation remains Phase 3; live
skill mutation and accepted-lineage writes start here under controlled gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import skillopt_propose as propose
except ModuleNotFoundError:  # pragma: no cover - direct import fallback in tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import skillopt_propose as propose

SELECTION_SCHEMA = "skillopt-selection.v1"
APPLY_RUN_SCHEMA = "skillopt-apply-run.v1"
REJECTED_SCHEMA = "skillopt-rejected-edit.v1"
LINEAGE_SCHEMA = "skillopt-accepted-lineage.v1"
LOW_RISK_GAPS = ("missing_verification", "missing_input_contract", "weak_output_contract")
UNSAFE_GAPS = {"runtime_unmapped"}
SIDE_EFFECT_PATH_RE = re.compile(r"(?i)(hermes|paperwiki|discord|ec2|runtime/jobs\.yaml|runtime/agents\.yaml)")
SECRET_RE = re.compile(
    r"(?i)(/Users/|(?:^|\s)~/|Mobile Documents|discord(?:app)?\.com/api/webhooks/|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|api[_ -]?key|bot[_ -]?token|"
    r"relay[_ -]?read[_ -]?token|private email body|mailbox-only)"
)


@dataclass(frozen=True)
class Check:
    ok: bool
    errors: list[str]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def contains_secret(value: Any) -> bool:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return bool(SECRET_RE.search(text))


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_report_output_path(path: Path, root: Path, label: str) -> Check:
    resolved = path.resolve()
    protected = [root / ".codex/skills", root / "skills", root / "runtime"]
    for protected_root in protected:
        if is_relative_to(resolved, protected_root):
            return Check(False, [f"{label} must not be under protected skill/runtime surfaces"])
    allowed = root / ".omx/reports/skillopt"
    tmp_roots = {Path("/tmp").resolve(), Path("/private/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if is_relative_to(resolved, allowed) or any(is_relative_to(resolved, tmp) for tmp in tmp_roots):
        return Check(True, [])
    return Check(False, [f"{label} must be under .omx/reports/skillopt or a temporary directory"])


def skill_path(root: Path, proposal: dict[str, Any]) -> Path:
    raw_text = str(proposal.get("skill_path", ""))
    raw_segments = raw_text.split("/")
    if any(segment in {"", ".", ".."} for segment in raw_segments):
        raise ValueError("skill path must not contain traversal, dot, or empty path segments")
    raw = Path(raw_text)
    if raw.is_absolute():
        raise ValueError("absolute skill paths are not allowed")
    path = (root / raw).resolve()
    if not is_relative_to(path, root):
        raise ValueError("skill path escapes repository root")
    rel_parts = path.relative_to(root.resolve()).parts
    allowed_skill_file = (
        len(rel_parts) == 4
        and rel_parts[0] == ".codex"
        and rel_parts[1] == "skills"
        and rel_parts[3] == "SKILL.md"
    ) or (
        len(rel_parts) == 3
        and rel_parts[0] == "skills"
        and rel_parts[2] in {"SKILL.md", "README.md"}
    )
    if not allowed_skill_file:
        raise ValueError("skill path must target an approved skill surface")
    if not path.is_file():
        raise ValueError("skill path does not exist")
    return path


def validate_proposal_file(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        proposal = read_json(path)
    except Exception as exc:
        return None, [f"invalid proposal json: {exc}"]
    result = propose.validate_proposal(proposal)
    return proposal, list(result.errors)


def heading_matches(line: str, target: str) -> bool:
    m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
    return bool(m and m.group(2).strip().lower() == target.strip().lower())


def section_spans(text: str, target: str) -> list[tuple[int, int]]:
    lines = text.splitlines(keepends=True)
    starts = [idx for idx, line in enumerate(lines) if heading_matches(line, target)]
    spans: list[tuple[int, int]] = []
    for start in starts:
        level = len(re.match(r"^(#{1,6})", lines[start]).group(1))  # type: ignore[union-attr]
        end = len(lines)
        for idx in range(start + 1, len(lines)):
            m = re.match(r"^(#{1,6})\s+", lines[idx])
            if m and len(m.group(1)) <= level:
                end = idx
                break
        spans.append((start, end))
    return spans


def normalize_patch(patch: str) -> str:
    return "\n".join(line.rstrip() for line in patch.strip().splitlines()) + "\n"


def apply_patch_text(text: str, proposal: dict[str, Any]) -> tuple[str | None, list[str], str]:
    target = str(proposal.get("target_section", "")).strip()
    patch = normalize_patch(str(proposal.get("patch", "")))
    edit_type = str(proposal.get("edit_type", ""))
    spans = section_spans(text, target)
    lines = text.splitlines(keepends=True)
    if edit_type in {"replace", "delete"} and len(spans) != 1:
        return None, ["target section is missing or ambiguous"], ""
    if edit_type == "replace":
        if not patch.strip():
            return None, ["replace patch must be non-empty"], ""
        start, end = spans[0]
        next_lines = lines[:start] + [patch if patch.endswith("\n") else patch + "\n"] + lines[end:]
        return "".join(next_lines), [], "replace"
    if edit_type == "delete":
        start, end = spans[0]
        next_lines = lines[:start] + lines[end:]
        return "".join(next_lines), [], "delete"
    if edit_type == "add":
        if len(spans) > 1:
            return None, ["target section is ambiguous"], ""
        insert = patch if patch.endswith("\n") else patch + "\n"
        if spans:
            _start, end = spans[0]
            prefix = lines[:end]
            suffix = lines[end:]
            if prefix and not prefix[-1].endswith("\n"):
                prefix[-1] += "\n"
            next_lines = prefix + (["\n"] if prefix and prefix[-1].strip() else []) + [insert] + suffix
            return "".join(next_lines), [], "add-after-section"
        sep = "" if text.endswith("\n") or not text else "\n"
        return text + sep + "\n" + insert, [], "append-end"
    return None, ["edit_type must be add, replace, or delete"], ""


def approval_status(proposal: dict[str, Any]) -> str:
    reviewer = str(proposal.get("reviewer_verdict", proposal.get("reviewer_status", ""))).upper()
    critic = str(proposal.get("critic_verdict", proposal.get("critic_status", ""))).upper()
    if reviewer == "REJECT" or critic == "REJECT":
        return "rejected"
    if reviewer == "APPROVE" and critic == "APPROVE":
        return "approved"
    return "pending"


def changed_line_count(patch: str) -> int:
    return sum(1 for line in patch.splitlines() if line.strip())


def classify_candidate(root: Path, path: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    proposal, errors = validate_proposal_file(path)
    base = {"path": path.as_posix(), "reason": ""}
    if proposal is None:
        base["reason"] = "; ".join(errors)
        return None, base
    base.update({
        "proposal_id": proposal.get("proposal_id"),
        "fingerprint": proposal.get("fingerprint"),
        "skill_path": proposal.get("skill_path"),
    })
    if errors:
        base["reason"] = "; ".join(errors)
        return None, base
    if contains_secret(proposal):
        base["reason"] = "privacy-risk text"
        return None, base
    gaps = set(proposal.get("source_gap_codes") or [])
    if proposal.get("edit_type") == "delete":
        base["reason"] = "delete proposals are excluded from first pilot selection"
        return None, base
    if gaps & UNSAFE_GAPS:
        base["reason"] = "runtime_unmapped proposals are excluded from first pilot selection"
        return None, base
    if SIDE_EFFECT_PATH_RE.search(str(proposal.get("skill_path", ""))):
        base["reason"] = "side-effect or critical runtime path excluded"
        return None, base
    status = approval_status(proposal)
    if status == "rejected":
        base["reason"] = "explicit reviewer/critic rejection"
        return None, base
    try:
        target = skill_path(root, proposal)
    except ValueError as exc:
        base["reason"] = str(exc)
        return None, base
    text = target.read_text(encoding="utf-8")
    actual = sha256_text(text)
    if actual != proposal.get("baseline_sha256"):
        base["reason"] = "stale baseline hash"
        return None, base
    if proposal.get("edit_type") in {"replace", "delete"} and len(section_spans(text, str(proposal.get("target_section", "")))) != 1:
        base["reason"] = "ambiguous target section"
        return None, base
    after_text, patch_errors, _operation = apply_patch_text(text, proposal)
    if patch_errors or after_text is None:
        base["reason"] = "; ".join(patch_errors) or "patch cannot be applied safely"
        return None, base
    if contains_secret(after_text):
        base["reason"] = "privacy-risk text after apply preview"
        return None, base
    rank = (
        0 if any(gap in LOW_RISK_GAPS for gap in gaps) else 1,
        0 if not SIDE_EFFECT_PATH_RE.search(str(proposal.get("skill_path", ""))) else 1,
        changed_line_count(str(proposal.get("patch", ""))),
        str(proposal.get("proposal_id", "")),
    )
    eligible = {
        **base,
        "approval_status": status,
        "rank": list(rank),
        "reason": "eligible low-risk controlled apply candidate" if rank[0] == 0 else "eligible fallback candidate",
        "source_gap_codes": sorted(gaps),
    }
    return eligible, None


def load_selection(path: Path) -> dict[str, Any]:
    selection = read_json(path)
    if selection.get("schema_version") != SELECTION_SCHEMA:
        raise ValueError("selection report schema_version must be skillopt-selection.v1")
    if not isinstance(selection.get("chosen"), dict):
        raise ValueError("selection report must include chosen object")
    return selection


def sanitize_chosen(chosen: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_id": chosen.get("proposal_id"),
        "fingerprint": chosen.get("fingerprint"),
        "skill_path": chosen.get("skill_path"),
        "approval_status": chosen.get("approval_status"),
        "reason": chosen.get("reason"),
        "source_gap_codes": chosen.get("source_gap_codes", []),
        "rank": chosen.get("rank", []),
    }


def assert_selection_matches(selection: dict[str, Any], proposal: dict[str, Any]) -> None:
    if contains_secret(selection):
        raise ValueError("selection report contains private path/secret-like data")
    chosen = selection.get("chosen") or {}
    if chosen.get("proposal_id") != proposal.get("proposal_id") or chosen.get("fingerprint") != proposal.get("fingerprint"):
        raise ValueError("proposal does not match --selection-report.chosen")


def eval_passes(report: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != "skillopt-eval.v1":
        errors.append(f"{label}.schema_version must be skillopt-eval.v1")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        errors.append(f"{label}.summary is required")
    else:
        status = str(summary.get("status", "")).upper()
        failed = int(summary.get("failed", 0) or 0)
        if status not in {"PASS", "PASSED"} or failed:
            errors.append(f"{label} must have passing summary")
    if contains_secret(report):
        errors.append(f"{label} contains private path/secret-like data")
    return errors


def cmd_select(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out = Path(args.out)
    guard = validate_report_output_path(out, root, "--out")
    if not guard.ok:
        for error in guard.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    candidate_dir = Path(args.candidate_dir)
    candidates = sorted(candidate_dir.glob("**/*.json"))
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for path in candidates:
        item, reject = classify_candidate(root, path)
        if item:
            eligible.append(item)
        if reject:
            rejected.append(reject)
    eligible.sort(key=lambda item: tuple(item["rank"]))
    chosen = eligible[0] if eligible else None
    report = {
        "schema_version": SELECTION_SCHEMA,
        "generated_at": args.as_of or now_iso(),
        "candidate_dir": str(candidate_dir),
        "policy": [
            "exclude delete/runtime_unmapped/stale/invalid/ambiguous/privacy/explicit-reject/side-effect candidates",
            "prefer missing_verification/missing_input_contract/weak_output_contract",
            "prefer non-critical covered skill and smallest changed-line count",
            "tie-break by deterministic proposal id",
        ],
        "rejected": rejected,
        "eligible": eligible,
        "chosen": chosen,
    }
    if contains_secret(report):
        print("selection report validation: FAIL", file=sys.stderr)
        print("FAIL: selection report contains private path/secret-like data", file=sys.stderr)
        return 1
    write_json(out, report)
    if not chosen:
        print("selection: FAIL no eligible candidates", file=sys.stderr)
        return 1
    print(json.dumps({"selected": chosen["proposal_id"], "fingerprint": chosen["fingerprint"]}, ensure_ascii=False, sort_keys=True))
    return 0


def build_run(proposal: dict[str, Any], selection: dict[str, Any], mode: str, before: str, after: str | None, diff_summary: dict[str, Any], errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": APPLY_RUN_SCHEMA,
        "run_id": f"skillopt-{mode}-{sha256_text((proposal.get('proposal_id','') + now_iso()))[:12]}",
        "mode": mode,
        "generated_at": now_iso(),
        "proposal_id": proposal.get("proposal_id"),
        "fingerprint": proposal.get("fingerprint"),
        "skill": proposal.get("skill"),
        "skill_path": proposal.get("skill_path"),
        "before_sha256": before,
        "after_sha256": after,
        "selection": sanitize_chosen(selection.get("chosen") or {}),
        "diff_summary": diff_summary,
        "errors": errors or [],
        "privacy_sanitized": True,
    }


def prepare(args: argparse.Namespace) -> tuple[Path, dict[str, Any], dict[str, Any], str, str, str, list[str]]:
    root = Path(args.root).resolve()
    proposal, errors = validate_proposal_file(Path(args.proposal))
    if proposal is None:
        return root, {}, {}, "", "", "", errors
    if errors:
        return root, proposal, {}, "", "", "", errors
    try:
        selection = load_selection(Path(args.selection_report))
        assert_selection_matches(selection, proposal)
        target = skill_path(root, proposal)
    except Exception as exc:
        return root, proposal, {}, "", "", "", [str(exc)]
    before_text = target.read_text(encoding="utf-8")
    before = sha256_text(before_text)
    if before != proposal.get("baseline_sha256"):
        return root, proposal, selection, before, "", "", ["stale baseline hash"]
    after_text, patch_errors, operation = apply_patch_text(before_text, proposal)
    if patch_errors or after_text is None:
        return root, proposal, selection, before, "", "", patch_errors
    if contains_secret(after_text) or contains_secret(proposal):
        return root, proposal, selection, before, "", "", ["privacy-risk text"]
    return root, proposal, selection, before, after_text, operation, []


def cmd_dry_run(args: argparse.Namespace) -> int:
    root, proposal, selection, before, after_text, operation, errors = prepare(args)
    out = Path(args.out)
    guard = validate_report_output_path(out, root, "--out")
    if not guard.ok:
        for error in guard.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    after = sha256_text(after_text) if after_text else None
    run = build_run(proposal, selection, "dry-run", before, after, {"operation": operation, "would_write": False}, errors)
    write_json(out, run)
    if errors:
        print("dry-run: FAIL", file=sys.stderr)
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("dry-run: PASS")
    return 0



def validate_dry_run_report(path: Path, proposal: dict[str, Any], selection: dict[str, Any], before: str, after: str | None, operation: str) -> list[str]:
    if not path.exists():
        return ["dry-run report is required before apply"]
    try:
        report = read_json(path)
    except Exception as exc:
        return [f"invalid dry-run report json: {exc}"]
    errors: list[str] = []
    if report.get("schema_version") != APPLY_RUN_SCHEMA:
        errors.append("dry-run report schema_version mismatch")
    if report.get("mode") != "dry-run":
        errors.append("dry-run report mode must be dry-run")
    if report.get("proposal_id") != proposal.get("proposal_id"):
        errors.append("dry-run report proposal_id mismatch")
    if report.get("fingerprint") != proposal.get("fingerprint"):
        errors.append("dry-run report fingerprint mismatch")
    if report.get("skill_path") != proposal.get("skill_path"):
        errors.append("dry-run report skill_path mismatch")
    if report.get("before_sha256") != before:
        errors.append("dry-run report before_sha256 mismatch")
    if report.get("after_sha256") != after:
        errors.append("dry-run report after_sha256 mismatch")
    if report.get("errors") not in ([], None):
        errors.append("dry-run report must have no errors")
    diff = report.get("diff_summary") or {}
    if diff.get("operation") != operation:
        errors.append("dry-run report operation mismatch")
    if diff.get("would_write") is not False:
        errors.append("dry-run report must be non-mutating")
    chosen = sanitize_chosen(selection.get("chosen") or {})
    if report.get("selection") != chosen:
        errors.append("dry-run report selection mismatch")
    if contains_secret(report):
        errors.append("dry-run report contains private path/secret-like data")
    return errors

def cmd_apply(args: argparse.Namespace) -> int:
    root, proposal, selection, before, after_text, operation, errors = prepare(args)
    if args.reviewer_verdict != "APPROVE":
        errors.append("reviewer_verdict must be APPROVE")
    if args.critic_verdict != "APPROVE":
        errors.append("critic_verdict must be APPROVE")
    if proposal.get("edit_type") == "delete" and args.allow_delete is not True:
        errors.append("delete requires --allow-delete")
    after = sha256_text(after_text) if after_text else None
    errors.extend(validate_dry_run_report(Path(args.dry_run_report), proposal, selection, before, after, operation))
    eval_before = read_json(Path(args.eval_before)) if Path(args.eval_before).exists() else {}
    eval_after = read_json(Path(args.eval_after)) if Path(args.eval_after).exists() else {}
    errors.extend(eval_passes(eval_before, "eval_before"))
    errors.extend(eval_passes(eval_after, "eval_after"))
    out = Path(args.out)
    lineage = Path(args.lineage)
    for path, label in ((out, "--out"), (lineage, "--lineage")):
        guard = validate_report_output_path(path, root, label)
        if not guard.ok:
            for error in guard.errors:
                print(f"FAIL: {error}", file=sys.stderr)
            return 1
    run = build_run(proposal, selection, "apply", before, after, {"operation": operation, "would_write": True}, errors)
    run["reviewer_verdict"] = args.reviewer_verdict
    run["critic_verdict"] = args.critic_verdict
    if errors:
        try:
            write_json(out, run)
        except Exception as exc:
            print("apply report write: FAIL", file=sys.stderr)
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
        print("apply: FAIL", file=sys.stderr)
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    record = {
        "schema_version": LINEAGE_SCHEMA,
        "proposal_selection_rationale": sanitize_chosen(selection.get("chosen") or {}),
        "proposal_id": proposal.get("proposal_id"),
        "fingerprint": proposal.get("fingerprint"),
        "skill": proposal.get("skill"),
        "skill_path": proposal.get("skill_path"),
        "before_sha256": before,
        "after_sha256": after,
        "eval_before": {"schema_version": eval_before.get("schema_version"), "summary": eval_before.get("summary")},
        "eval_after": {"schema_version": eval_after.get("schema_version"), "summary": eval_after.get("summary")},
        "reviewer_verdict": args.reviewer_verdict,
        "critic_verdict": args.critic_verdict,
        "apply_run_id": run["run_id"],
        "commit_id": args.commit_id or None,
        "accepted_at": args.as_of or now_iso(),
    }
    result = propose.validate_lineage_record(record)
    if not result.ok:
        try:
            failure = build_run(proposal, selection, "apply", before, after, {"operation": operation, "would_write": True}, result.errors)
            failure["reviewer_verdict"] = args.reviewer_verdict
            failure["critic_verdict"] = args.critic_verdict
            write_json(out, failure)
        except Exception:
            pass
        print("lineage validation: FAIL", file=sys.stderr)
        for error in result.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1

    target: Path | None = None
    original = ""
    try:
        target = skill_path(root, proposal)
        original = target.read_text(encoding="utf-8")
        target.write_text(after_text, encoding="utf-8")
        if args.simulate_fail_before_lineage:
            raise RuntimeError("simulated failure before lineage append")
        write_json(out, run)
        append_jsonl(lineage, record)
    except Exception as exc:
        if target is not None:
            target.write_text(original, encoding="utf-8")
        try:
            failure = build_run(proposal, selection, "apply", before, after, {"operation": operation, "would_write": True}, [str(exc)])
            failure["reviewer_verdict"] = args.reviewer_verdict
            failure["critic_verdict"] = args.critic_verdict
            if not out.exists() or out.is_file():
                write_json(out, failure)
        except Exception:
            pass
        print("apply: FAIL", file=sys.stderr)
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("apply: PASS")
    return 0

def cmd_reject(args: argparse.Namespace) -> int:
    proposal, errors = validate_proposal_file(Path(args.proposal))
    if proposal is None or errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    record = {
        "schema_version": REJECTED_SCHEMA,
        "proposal_id": proposal["proposal_id"],
        "fingerprint": proposal["fingerprint"],
        "skill": proposal["skill"],
        "skill_path": proposal["skill_path"],
        "baseline_sha256": proposal["baseline_sha256"],
        "rejected_at": args.as_of or now_iso(),
        "reason": args.reason,
        "reviewer": args.reviewer,
        "source_gap_codes": proposal["source_gap_codes"],
    }
    result = propose.validate_rejected(record)
    if not result.ok:
        for error in result.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    root = Path(args.root).resolve()
    buffer = Path(args.buffer)
    guard = validate_report_output_path(buffer, root, "--buffer")
    if not guard.ok:
        for error in guard.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    append_jsonl(buffer, record)
    print("rejected edit appended")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SkillOpt Phase 4 controlled apply")
    sub = parser.add_subparsers(dest="command", required=True)
    select = sub.add_parser("select")
    select.add_argument("--candidate-dir", required=True)
    select.add_argument("--out", required=True)
    select.add_argument("--root", default=".")
    select.add_argument("--as-of", default="")

    dry = sub.add_parser("dry-run")
    dry.add_argument("proposal")
    dry.add_argument("--selection-report", required=True)
    dry.add_argument("--out", required=True)
    dry.add_argument("--root", default=".")

    apply = sub.add_parser("apply")
    apply.add_argument("proposal")
    apply.add_argument("--selection-report", required=True)
    apply.add_argument("--dry-run-report", required=True)
    apply.add_argument("--reviewer-verdict", required=True)
    apply.add_argument("--critic-verdict", required=True)
    apply.add_argument("--eval-before", required=True)
    apply.add_argument("--eval-after", required=True)
    apply.add_argument("--lineage", required=True)
    apply.add_argument("--out", required=True)
    apply.add_argument("--root", default=".")
    apply.add_argument("--commit-id", default="")
    apply.add_argument("--as-of", default="")
    apply.add_argument("--allow-delete", action="store_true")
    apply.add_argument("--simulate-fail-before-lineage", action="store_true", help=argparse.SUPPRESS)

    reject = sub.add_parser("reject")
    reject.add_argument("proposal")
    reject.add_argument("--reason", required=True)
    reject.add_argument("--reviewer", default="human")
    reject.add_argument("--buffer", default=".omx/reports/skillopt/rejected-edits.jsonl")
    reject.add_argument("--root", default=".")
    reject.add_argument("--as-of", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "select":
        return cmd_select(args)
    if args.command == "dry-run":
        return cmd_dry_run(args)
    if args.command == "apply":
        return cmd_apply(args)
    if args.command == "reject":
        return cmd_reject(args)
    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
