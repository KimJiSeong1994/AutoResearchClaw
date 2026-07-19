#!/usr/bin/env python3
"""SkillOpt Phase 3 bounded patch proposal queue.

This control-plane script creates deterministic, review-gated proposal records
from SkillOpt audit/eval evidence. It does not mutate skill files. Real skill
application and live accepted-lineage writes belong to Phase 4 controlled apply.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:
    from skillopt_common import (
        HEX64_RE, ValidationResult,
        is_relative_to, now_iso,
        read_json, sha256_text, validate_report_output_path, write_json,
    )
except ModuleNotFoundError:  # pragma: no cover - direct path fallback in tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from skillopt_common import (
        HEX64_RE, ValidationResult,
        is_relative_to, now_iso,
        read_json, sha256_text, validate_report_output_path, write_json,
    )

PROPOSAL_SCHEMA = "skillopt-proposal.v1"
REJECTED_SCHEMA = "skillopt-rejected-edit.v1"
LINEAGE_SCHEMA = "skillopt-accepted-lineage.v1"
EDIT_TYPES = {"add", "delete", "replace"}
RISKS = {"low", "medium", "high"}
# HEX64_RE imported from skillopt_common
SECRET_RE = re.compile(
    r"(?i)(/Users/|(?:^|\s)~/|Mobile Documents|discord(?:app)?\.com/api/webhooks/|"
    r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|api[_ -]?key|bot[_ -]?token|"
    r"relay[_ -]?read[_ -]?token|private email body|mailbox-only)"
)

TEMPLATES: dict[str, dict[str, str]] = {
    "missing_verification": {
        "edit_type": "add",
        "target_section": "Verification",
        "risk": "low",
        "patch": "## Verification\n\n- Run the skill-specific validator or checklist before accepting changes.\n- Record command output or reviewer evidence in the SkillOpt evaluation report.\n",
        "rationale": "Add an explicit verification checklist because the audit found no verification/checklist section.",
    },
    "missing_failure_rollback": {
        "edit_type": "add",
        "target_section": "Failure and rollback",
        "risk": "low",
        "patch": "## Failure and rollback\n\n- If required inputs or evidence are missing, stop and return a needs_review result instead of guessing.\n- Do not perform production writes; preserve the prior artifact and record the blocker.\n",
        "rationale": "Add fallback/rollback guidance because the audit found no failure or rollback signal.",
    },
    "privacy_boundary_missing": {
        "edit_type": "add",
        "target_section": "Safety and privacy",
        "risk": "medium",
        "patch": "## Safety and privacy\n\n- Keep public outputs separate from internal evidence.\n- Do not expose local paths, confidential source text, credential values, or webhook URLs.\n",
        "rationale": "Add explicit public/private boundary guidance because the audit found no safety/privacy section.",
    },
    "weak_output_contract": {
        "edit_type": "add",
        "target_section": "Output contract",
        "risk": "low",
        "patch": "## Output contract\n\nReturn a compact artifact with the requested result, evidence links or reasons, and any explicit verification gaps.\n",
        "rationale": "Add an output contract skeleton because the audit found no clear output contract section.",
    },
    "missing_input_contract": {
        "edit_type": "add",
        "target_section": "Input contract",
        "risk": "low",
        "patch": "## Input contract\n\nBefore running the workflow, identify the user request, source material, required metadata, and any missing facts that must stay marked as unknown.\n",
        "rationale": "Add an input contract skeleton because the audit found no clear input contract section.",
    },
    "runtime_unmapped": {
        "edit_type": "add",
        "target_section": "Runtime linkage review",
        "risk": "medium",
        "patch": "## Runtime linkage review\n\nReview whether this skill should be referenced by `runtime/agents.yaml` or `runtime/jobs.yaml`. Do not edit runtime manifests from the proposal queue; route manifest changes through a separate reviewed implementation step.\n",
        "rationale": "Create a runtime linkage review item because the audit found no runtime agent/job reference for this skill surface.",
    },
}


# ValidationResult, now_iso, read_json, write_json, sha256_text imported from skillopt_common


def rel_display(path_value: str, root: Path) -> str:
    if not path_value:
        return ""
    path = Path(path_value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name
    return path_value


# is_relative_to and validate_report_output_path imported from skillopt_common


def slug(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return safe[:80] or "proposal"


def contains_secret(value: Any) -> bool:
    return bool(SECRET_RE.search(json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value))


def privacy_errors(record: Any) -> list[str]:
    return ["record contains private path/secret-like data"] if contains_secret(record) else []


def proposal_fingerprint(proposal: dict[str, Any]) -> str:
    basis = {
        "schema_version": proposal.get("schema_version"),
        "skill_path": proposal.get("skill_path"),
        "baseline_sha256": proposal.get("baseline_sha256"),
        "edit_type": proposal.get("edit_type"),
        "target_section": proposal.get("target_section"),
        "patch": normalize_patch(str(proposal.get("patch", ""))),
        "source_gap_codes": sorted(proposal.get("source_gap_codes", [])),
    }
    return sha256_text(json.dumps(basis, ensure_ascii=False, sort_keys=True))


def normalize_patch(patch: str) -> str:
    return "\n".join(line.rstrip() for line in patch.strip().splitlines()) + "\n"


def validate_proposal(proposal: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    if proposal.get("schema_version") != PROPOSAL_SCHEMA:
        errors.append("schema_version must be skillopt-proposal.v1")
    for field in ("proposal_id", "skill", "skill_path", "baseline_sha256", "target_section", "rationale", "patch"):
        if not str(proposal.get(field, "")).strip():
            errors.append(f"missing required field: {field}")
    if proposal.get("edit_type") not in EDIT_TYPES:
        errors.append("edit_type must be add, delete, or replace")
    if proposal.get("risk") not in RISKS:
        errors.append("risk must be low, medium, or high")
    if not HEX64_RE.match(str(proposal.get("baseline_sha256", ""))):
        errors.append("baseline_sha256 must be a 64-character lowercase sha256 hex string")
    if not proposal.get("evidence") or not isinstance(proposal.get("evidence"), list):
        errors.append("evidence must be a non-empty list")
    if not proposal.get("source_gap_codes") or not isinstance(proposal.get("source_gap_codes"), list):
        errors.append("source_gap_codes must be a non-empty list")
    if proposal.get("requires_human_review") is not True:
        errors.append("requires_human_review must be true")
    if proposal.get("privacy_sanitized") is not True:
        errors.append("privacy_sanitized must be true")
    gate = proposal.get("review_gate", {})
    if gate.get("reviewer_required") is not True:
        errors.append("review_gate.reviewer_required must be true")
    if gate.get("critic_required") is not True:
        errors.append("review_gate.critic_required must be true")
    if gate.get("automatic_accept") is not False:
        errors.append("review_gate.automatic_accept must be false")
    expected_fp = proposal_fingerprint(proposal)
    if proposal.get("fingerprint") and not HEX64_RE.match(str(proposal.get("fingerprint"))):
        errors.append("fingerprint must be a 64-character lowercase sha256 hex string")
    if proposal.get("fingerprint") and proposal.get("fingerprint") != expected_fp:
        errors.append("fingerprint does not match proposal content")
    if proposal.get("proposal_id") and not str(proposal["proposal_id"]).endswith(expected_fp[:12]):
        errors.append("proposal_id must include the fingerprint prefix")
    errors.extend(privacy_errors(proposal))
    return ValidationResult(not errors, errors)


def validate_rejected(record: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    if record.get("schema_version") != REJECTED_SCHEMA:
        errors.append("schema_version must be skillopt-rejected-edit.v1")
    for field in ("proposal_id", "fingerprint", "skill", "skill_path", "baseline_sha256", "rejected_at", "reason", "reviewer"):
        if not str(record.get(field, "")).strip():
            errors.append(f"missing required field: {field}")
    if not HEX64_RE.match(str(record.get("baseline_sha256", ""))):
        errors.append("baseline_sha256 must be sha256 hex")
    if not record.get("source_gap_codes"):
        errors.append("source_gap_codes required")
    if record.get("fingerprint") and not HEX64_RE.match(str(record.get("fingerprint"))):
        errors.append("fingerprint must be sha256 hex")
    errors.extend(privacy_errors(record))
    return ValidationResult(not errors, errors)


def validate_lineage_record(record: dict[str, Any]) -> ValidationResult:
    errors: list[str] = []
    if record.get("schema_version") != LINEAGE_SCHEMA:
        errors.append("schema_version must be skillopt-accepted-lineage.v1")
    for field in ("proposal_id", "fingerprint", "skill", "skill_path", "before_sha256", "after_sha256", "eval_before", "eval_after", "reviewer_verdict", "critic_verdict", "accepted_at"):
        if field not in record or record.get(field) in ("", None):
            errors.append(f"missing required field: {field}")
    for field in ("before_sha256", "after_sha256"):
        if not HEX64_RE.match(str(record.get(field, ""))):
            errors.append(f"{field} must be sha256 hex")
    if record.get("reviewer_verdict") != "APPROVE":
        errors.append("reviewer_verdict must be APPROVE")
    if record.get("critic_verdict") != "APPROVE":
        errors.append("critic_verdict must be APPROVE")
    if record.get("before_sha256") == record.get("after_sha256") and record.get("metadata_only") is not True:
        errors.append("before_sha256 and after_sha256 must differ unless metadata_only is true")
    if record.get("fingerprint") and not HEX64_RE.match(str(record.get("fingerprint"))):
        errors.append("fingerprint must be sha256 hex")
    for field in ("eval_before", "eval_after"):
        value = record.get(field)
        if not isinstance(value, dict) or not value.get("schema_version") or not value.get("summary"):
            errors.append(f"{field} must include schema_version and summary")
    errors.extend(privacy_errors(record))
    return ValidationResult(not errors, errors)


def load_rejected_fingerprints(path: Path) -> set[str]:
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("fingerprint"):
            out.add(str(item["fingerprint"]))
    return out


def proposal_for_gap(skill: dict[str, Any], gap_code: str, audit_path: str, eval_report: dict[str, Any], eval_path: str, as_of: str, root: Path) -> dict[str, Any] | None:
    template = TEMPLATES.get(gap_code)
    if not template:
        return None
    proposal: dict[str, Any] = {
        "schema_version": PROPOSAL_SCHEMA,
        "proposal_id": "",
        "skill": skill.get("name", ""),
        "skill_path": skill.get("path", ""),
        "baseline_sha256": skill.get("content_sha256", ""),
        "edit_type": template["edit_type"],
        "target_section": template["target_section"],
        "rationale": template["rationale"],
        "patch": template["patch"],
        "evidence": [
            f"audit:{rel_display(audit_path, root)}#{gap_code}",
            f"eval:{rel_display(eval_path, root)}#summary",
        ],
        "risk": template["risk"],
        "requires_human_review": True,
        "review_gate": {
            "reviewer_required": True,
            "critic_required": True,
            "automatic_accept": False,
        },
        "source_gap_codes": [gap_code],
        "eval_summary": eval_report.get("summary", {}),
        "privacy_sanitized": True,
        "generated_at": as_of,
        "phase_boundary": "proposal_only_no_skill_mutation_phase4_apply_required",
    }
    fp = proposal_fingerprint(proposal)
    proposal["fingerprint"] = fp
    proposal["proposal_id"] = f"skillopt-{slug(str(skill.get('name') or skill.get('path')))}-{fp[:12]}"
    return proposal


def current_skill_hash(root: Path, skill_path: str) -> tuple[str | None, str | None]:
    path = Path(skill_path)
    if path.is_absolute():
        return None, "absolute skill paths are not allowed in proposal generation"
    resolved = (root / path).resolve()
    if not is_relative_to(resolved, root):
        return None, "skill path escapes repository root"
    if not resolved.exists() or not resolved.is_file():
        return None, "skill path does not exist"
    return sha256_text(resolved.read_text(encoding="utf-8")), None


def generate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    audit_path = Path(args.audit)
    eval_path = Path(args.eval)
    audit = read_json(audit_path)
    eval_report = read_json(eval_path)
    as_of = args.as_of or audit.get("generated_at") or eval_report.get("generated_at") or now_iso()
    out_dir = Path(args.out_dir)
    out_guard = validate_report_output_path(out_dir, root, "--out-dir")
    if not out_guard.ok:
        print("output path validation: FAIL", file=sys.stderr)
        for error in out_guard.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    rejected = load_rejected_fingerprints(Path(args.rejected_buffer))
    proposals: list[dict[str, Any]] = []
    seen_generated: set[str] = set()
    for skill in sorted(audit.get("skills", []), key=lambda item: item.get("path", "")):
        actual_hash, hash_error = current_skill_hash(root, str(skill.get("path", "")))
        if hash_error:
            print(f"skill baseline validation: FAIL for {slug(str(skill.get('name') or 'unknown'))}", file=sys.stderr)
            print(f"FAIL: {hash_error}", file=sys.stderr)
            return 1
        if actual_hash != skill.get("content_sha256"):
            print(f"skill baseline validation: FAIL for {slug(str(skill.get('name') or skill.get('path') or 'unknown'))}", file=sys.stderr)
            print("FAIL: audit baseline hash does not match current skill file", file=sys.stderr)
            return 1
        for gap in skill.get("gaps", []):
            proposal = proposal_for_gap(skill, gap.get("gap_code", ""), str(audit_path), eval_report, str(eval_path), as_of, root)
            if not proposal:
                continue
            if proposal["fingerprint"] in seen_generated:
                continue
            seen_generated.add(proposal["fingerprint"])
            if proposal["fingerprint"] in rejected and not args.include_rejected:
                continue
            if proposal["fingerprint"] in rejected:
                proposal["previously_rejected"] = True
            result = validate_proposal(proposal)
            if not result.ok:
                print(f"invalid generated proposal for {slug(str(skill.get('name') or 'unknown'))}", file=sys.stderr)
                for error in result.errors:
                    print(f"FAIL: {error}", file=sys.stderr)
                return 1
            proposals.append(proposal)
    clean_managed_candidates(out_dir)
    written: list[str] = []
    for proposal in proposals:
        skill_dir = out_dir / slug(str(proposal["skill"] or proposal["skill_path"]))
        path = skill_dir / f"{proposal['proposal_id']}.json"
        write_json(path, proposal)
        written.append(rel_display(str(path), root))
    summary = {
        "schema_version": "skillopt-proposal-run.v1",
        "generated_at": as_of,
        "count": len(written),
        "written": written,
        "rejected_suppressed": len(rejected),
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0


def clean_managed_candidates(out_dir: Path) -> None:
    """Remove prior managed candidate JSON files so rejected suppression is visible in reused queues."""
    if not out_dir.exists():
        return
    for path in sorted(out_dir.glob("*/*.json")):
        try:
            payload = read_json(path)
        except Exception:
            continue
        if payload.get("schema_version") == PROPOSAL_SCHEMA and str(payload.get("proposal_id", "")).startswith("skillopt-"):
            path.unlink()


def cmd_validate_proposal(args: argparse.Namespace) -> int:
    result = validate_proposal(read_json(Path(args.proposal)))
    if result.ok:
        print("proposal validation: PASS")
        return 0
    print("proposal validation: FAIL")
    for error in result.errors:
        print(f"FAIL: {error}")
    return 1


def cmd_reject(args: argparse.Namespace) -> int:
    proposal = read_json(Path(args.proposal))
    result = validate_proposal(proposal)
    if not result.ok:
        print("proposal validation: FAIL", file=sys.stderr)
        for error in result.errors:
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
    rejected_result = validate_rejected(record)
    if not rejected_result.ok:
        print("rejected record validation: FAIL", file=sys.stderr)
        for error in rejected_result.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    buffer = Path(args.buffer)
    root = Path(args.root).resolve()
    buffer_guard = validate_report_output_path(buffer, root, "--buffer")
    if not buffer_guard.ok:
        print("buffer path validation: FAIL", file=sys.stderr)
        for error in buffer_guard.errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    buffer.parent.mkdir(parents=True, exist_ok=True)
    with buffer.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    print("rejected edit appended")
    print(f"fingerprint={record['fingerprint']}")
    return 0


def cmd_validate_lineage(args: argparse.Namespace) -> int:
    path = Path(args.lineage)
    errors: list[str] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {index}: invalid JSON: {exc}")
            continue
        result = validate_lineage_record(record)
        errors.extend(f"line {index}: {error}" for error in result.errors)
    if not errors:
        print("accepted lineage validation: PASS")
        return 0
    print("accepted lineage validation: FAIL")
    for error in errors:
        print(f"FAIL: {error}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SkillOpt Phase 3 proposal queue")
    sub = parser.add_subparsers(dest="command")

    validate_p = sub.add_parser("validate-proposal")
    validate_p.add_argument("proposal")

    reject_p = sub.add_parser("reject")
    reject_p.add_argument("proposal")
    reject_p.add_argument("--reason", required=True)
    reject_p.add_argument("--reviewer", default="human")
    reject_p.add_argument("--buffer", default=".omx/reports/skillopt/rejected-edits.jsonl")
    reject_p.add_argument("--as-of", default="")
    reject_p.add_argument("--root", default=".")

    lineage_p = sub.add_parser("validate-lineage")
    lineage_p.add_argument("lineage")

    parser.add_argument("--root", default=".")
    parser.add_argument("--audit", default=".omx/reports/skillopt/skillopt-audit-latest.json")
    parser.add_argument("--eval", default=".omx/reports/skillopt/skillopt-eval-latest.json")
    parser.add_argument("--out-dir", default=".omx/reports/skillopt/patch-candidates")
    parser.add_argument("--rejected-buffer", default=".omx/reports/skillopt/rejected-edits.jsonl")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--include-rejected", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "validate-proposal":
        return cmd_validate_proposal(args)
    if args.command == "reject":
        return cmd_reject(args)
    if args.command == "validate-lineage":
        return cmd_validate_lineage(args)
    return generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
