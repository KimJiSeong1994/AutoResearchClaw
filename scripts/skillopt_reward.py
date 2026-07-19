#!/usr/bin/env python3
"""Deterministic stdlib-only SkillOpt Phase 5 reward scorer.

The reward report is advisory: it reads audit/eval/proposal/lineage artifacts,
emits score-bearing diagnostics, and never mutates skills, runtime manifests, or
external systems.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

try:
    from skillopt_common import (
        HEX64_RE, LOW_RISK_GAPS, ValidationResult,
        changed_line_count, is_relative_to, now_iso,
        read_json, sha256_text, validate_report_output_path,
    )
except ModuleNotFoundError:  # pragma: no cover - direct path fallback in tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from skillopt_common import (
        HEX64_RE, LOW_RISK_GAPS, ValidationResult,
        changed_line_count, is_relative_to, now_iso,
        read_json, sha256_text, validate_report_output_path,
    )

SCHEMA_VERSION = "skillopt-reward.v1"
COMPONENT_KEYS = (
    "eval_quality_bp",
    "contract_quality_bp",
    "safety_bp",
    "stability_bp",
    "efficiency_bp",
    "lineage_bp",
    "runtime_risk_bp",
)
WEIGHTS = {
    "eval_quality_bp": 2600,
    "contract_quality_bp": 1700,
    "safety_bp": 1900,
    "stability_bp": 1300,
    "efficiency_bp": 900,
    "lineage_bp": 700,
    "runtime_risk_bp": 900,
}
# LOW_RISK_GAPS and HEX64_RE imported from skillopt_common
SIDE_EFFECT_PATH_RE = re.compile(r"(?i)(runtime/|deploy|service|bot|bridge|hermes|discord|ec2|aws|cron|crontab)")
FORBIDDEN_RE = re.compile(
    r"(?i)(/Users/|(?:^|[\s\"'])~/|Mobile Documents|discord(?:app)?\.com/api/webhooks/|"
    r"https://hooks\.slack\.com/services/|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]+|"
    r"api[_ -]?key|bot[_ -]?token|relay[_ -]?read[_ -]?token|private email body|"
    r"mailbox-only|private mailbox|raw email body|raw private body|private body)"
)
PRIVATE_PATH_RE = re.compile(r"/Users/[^\s\"'`|,}]+")
HOME_PATH_RE = re.compile(r"(?:(?<=\s)|^)~/[^\s\"'`|,}]+")


# ValidationResult and now_iso imported from skillopt_common


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# sha256_text imported from skillopt_common


def deterministic_id(prefix: str, basis: Any, length: int = 16) -> str:
    return f"{prefix}-{sha256_text(canonical_json(basis))[:length]}"


# read_json imported from skillopt_common
# write_json kept local: omits sort_keys (diverges from common's sort_keys=True variant)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# is_relative_to imported from skillopt_common


def rel_display(path_value: str | Path, root: Path) -> str:
    if not str(path_value):
        return ""
    path = Path(path_value)
    try:
        if path.is_absolute():
            return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name
    text = path.as_posix()
    return sanitize_text(text, root)


def sanitize_text(value: str, root: Path) -> str:
    safe = value.replace(str(root.resolve()), ".")
    safe = PRIVATE_PATH_RE.sub("[redacted-local-path]", safe)
    safe = HOME_PATH_RE.sub("[redacted-home-path]", safe)
    safe = safe.replace("Mobile Documents", "[redacted-local-path]")
    safe = re.sub(r"(?i)discord(?:app)?\.com/api/webhooks/[^\s\"'`]+", "[redacted-webhook]", safe)
    safe = re.sub(r"(?i)https://hooks\.slack\.com/services/[^\s\"'`]+", "[redacted-webhook]", safe)
    safe = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[redacted-token]", safe)
    safe = re.sub(r"xox[baprs]-[A-Za-z0-9-]+", "[redacted-token]", safe)
    safe = re.sub(r"(?i)(api[_ -]?key|bot[_ -]?token|relay[_ -]?read[_ -]?token)\s*[:=]\s*[^\s\"']+", r"\1=[redacted-token]", safe)
    safe = re.sub(r"(?is)(private email body|mailbox-only|private mailbox|raw email body|raw private body|private body).*", "[redacted-private-evidence]", safe)
    return safe


def sanitize_value(value: Any, root: Path) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, root)
    if isinstance(value, list):
        return [sanitize_value(item, root) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item, root) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_value(val, root) for key, val in value.items()}
    return value


def contains_forbidden(value: Any) -> bool:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    return bool(FORBIDDEN_RE.search(text))


def privacy_errors(record: Any) -> list[str]:
    return ["record contains forbidden private path/token/webhook/mailbox-only content"] if contains_forbidden(record) else []


# validate_report_output_path imported from skillopt_common


def validate_input_path(path: Path, root: Path, label: str) -> ValidationResult:
    resolved = path.resolve()
    tmp_roots = {Path("/tmp").resolve(), Path("/private/tmp").resolve(), Path(tempfile.gettempdir()).resolve()}
    if is_relative_to(resolved, root) or any(is_relative_to(resolved, tmp) for tmp in tmp_roots):
        return ValidationResult(True, [])
    return ValidationResult(False, [f"{label} must be under the repository root or a temporary directory"])


def load_optional_json(path_value: str, root: Path, label: str) -> tuple[Any | None, list[str]]:
    if not path_value:
        return None, []
    path = Path(path_value)
    guard = validate_input_path(path, root, label)
    if not guard.ok:
        return None, guard.errors
    try:
        data = read_json(path)
    except Exception as exc:  # noqa: BLE001 - CLI diagnostic should be concise.
        return None, [f"{label} could not be read as JSON: {exc}"]
    return sanitize_value(data, root), []


def load_jsonl(path_value: str, root: Path, label: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not path_value:
        return [], []
    path = Path(path_value)
    guard = validate_input_path(path, root, label)
    if not guard.ok:
        return [], guard.errors
    if not path.exists():
        return [], []
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            item = sanitize_value(json.loads(line), root)
            if isinstance(item, dict):
                rows.append(item)
            else:
                errors.append(f"{label}:{line_no} is not an object")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{label} could not be read as JSONL: {exc}")
    return rows, errors


def extract_eval_results(eval_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(eval_report, dict):
        return []
    raw = eval_report.get("results") or eval_report.get("case_results") or []
    return [item for item in raw if isinstance(item, dict)]


def audit_gap_counts(audit_report: dict[str, Any] | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not isinstance(audit_report, dict):
        return counts
    for row in audit_report.get("gap_matrix") or []:
        if not isinstance(row, dict):
            continue
        for code in row.get("gap_codes") or []:
            counts[str(code)] = counts.get(str(code), 0) + 1
    for record in audit_report.get("skills") or audit_report.get("records") or []:
        if not isinstance(record, dict):
            continue
        for gap in record.get("gaps") or []:
            if isinstance(gap, dict) and gap.get("gap_code"):
                code = str(gap["gap_code"])
                counts[code] = counts.get(code, 0) + 1
    return counts


def score_eval_components(eval_report: dict[str, Any] | None, audit_report: dict[str, Any] | None) -> tuple[dict[str, int], int, int, list[str], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    warnings: list[dict[str, Any]] = []
    penalties: list[dict[str, Any]] = []
    explanations: list[str] = []
    evidence: list[str] = []
    results = extract_eval_results(eval_report)
    summary = eval_report.get("summary", {}) if isinstance(eval_report, dict) else {}
    total = int(summary.get("total", len(results)) or 0) if isinstance(summary, dict) else len(results)
    failed = int(summary.get("failed", sum(1 for r in results if not r.get("passed"))) or 0) if isinstance(summary, dict) else sum(1 for r in results if not r.get("passed"))
    passed = max(0, total - failed)
    pass_bp = 0 if total <= 0 else (passed * 10000) // total
    if total <= 0:
        warnings.append({"code": "eval_absent", "severity": "warning", "message": "no eval cases were available"})
        penalties.append({"code": "eval_absent", "component": "eval_quality_bp", "bp": -3000})
        eval_quality = 2500
    else:
        eval_quality = pass_bp
        evidence.append(f"eval:{passed}/{total} cases passed")
        explanations.append(f"eval quality reflects {passed}/{total} passing held-out cases")
    gap_counts = audit_gap_counts(audit_report)
    total_gaps = sum(gap_counts.values())
    contract_penalty = min(6500, total_gaps * 350)
    contract_quality = clamp(10000 - contract_penalty, 0, 10000)
    if total_gaps:
        explanations.append(f"contract quality penalizes {total_gaps} audit gap(s)")
        evidence.append("audit:gap_matrix")
    safety_penalty = 0
    runtime_penalty = 0
    stability_penalty = 0
    for code, count in sorted(gap_counts.items()):
        if "privacy" in code or "unsanitized" in code:
            safety_penalty += 2200 * count
            penalties.append({"code": code, "component": "safety_bp", "bp": -2200 * count})
        if "runtime" in code or "side_effect" in code:
            runtime_penalty += 1800 * count
            penalties.append({"code": code, "component": "runtime_risk_bp", "bp": -1800 * count})
        if "rollback" in code or "failure" in code:
            stability_penalty += 900 * count
            penalties.append({"code": code, "component": "stability_bp", "bp": -900 * count})
    components = {
        "eval_quality_bp": clamp(eval_quality, 0, 10000),
        "contract_quality_bp": contract_quality,
        "safety_bp": clamp(10000 - safety_penalty, 0, 10000),
        "stability_bp": clamp(9000 - stability_penalty, 0, 10000),
        "efficiency_bp": 8000,
        "lineage_bp": 5000,
        "runtime_risk_bp": clamp(10000 - runtime_penalty, 0, 10000),
    }
    coverage_bp = clamp(2500 + min(total, 8) * 750 + min(total_gaps, 6) * 250, 0, 10000)
    confidence_bp = clamp((coverage_bp * 6 + pass_bp * 4) // 10 if total > 0 else 2500, 0, 10000)
    if total < 3:
        warnings.append({"code": "small_fixture_set", "severity": "warning", "message": "low case count reduces confidence"})
        confidence_bp = min(confidence_bp, 5500)
    explanations.extend([
        "eval_quality component follows held-out case pass rate",
        "contract_quality component follows audit gap density",
        "safety component applies privacy and secret penalties",
        "stability component applies no-regression and rollback signals",
        "efficiency component is conservative for eval-level reports",
        "lineage component is neutral for eval-level reports",
        "runtime_risk component penalizes runtime or side-effect signals",
    ])
    return components, confidence_bp, coverage_bp, explanations, warnings, penalties, evidence


def component_score_bp(components: dict[str, int]) -> int:
    weighted = sum(int(components.get(key, 0)) * WEIGHTS[key] for key in COMPONENT_KEYS)
    return clamp(round(weighted / 10000), -10000, 10000)


def ordered_components(values: dict[str, int]) -> dict[str, int]:
    return {key: clamp(values.get(key, 0), -10000, 10000) for key in COMPONENT_KEYS}


def make_record_run_id(record: dict[str, Any]) -> str:
    basis = {
        "schema_version": record.get("schema_version"),
        "report_type": record.get("report_type"),
        "generated_at": record.get("generated_at"),
        "source_identifiers": record.get("source_identifiers", {}),
    }
    return deterministic_id("reward", basis)


def finalize_record(record: dict[str, Any], root: Path) -> dict[str, Any]:
    record["components"] = ordered_components(record.get("components", {}))
    record["score_bp"] = component_score_bp(record["components"])
    record["confidence_bp"] = clamp(record.get("confidence_bp", 0), 0, 10000)
    record["coverage_bp"] = clamp(record.get("coverage_bp", 0), 0, 10000)
    record["privacy_sanitized"] = True
    record["policy"] = {"weights_bp": {key: WEIGHTS[key] for key in COMPONENT_KEYS}, "component_order": list(COMPONENT_KEYS), "advisory_only": True, "automatic_accept": False}
    record.setdefault("evidence", [])
    record.setdefault("explanations", [])
    record.setdefault("warnings", [])
    record.setdefault("penalties", [])
    record["run_id"] = make_record_run_id(record)
    sanitized = sanitize_value(record, root)
    errors = privacy_errors(sanitized)
    if errors:
        sanitized.setdefault("warnings", []).append({"code": "privacy_signal", "severity": "hard_gate", "message": "; ".join(errors)})
        sanitized["hard_gate_passed"] = False
    return sanitized


def eval_reward_record(args: argparse.Namespace, root: Path, audit_report: dict[str, Any] | None, eval_report: dict[str, Any] | None, generated_at: str) -> dict[str, Any]:
    components, confidence_bp, coverage_bp, explanations, warnings, penalties, evidence = score_eval_components(eval_report, audit_report)
    results = extract_eval_results(eval_report)
    skills = sorted({str(item.get("skill", "unknown")) for item in results if item.get("skill")})
    case_results: list[dict[str, Any]] = []
    for item in sorted(results, key=lambda r: (str(r.get("skill", "")), str(r.get("case_id", "")))):
        case_results.append({
            "skill": str(item.get("skill", "unknown")),
            "case_id": str(item.get("case_id", "unknown")),
            "passed": bool(item.get("passed")),
            "errors": sanitize_value(item.get("errors", []), root),
        })
    eval_ref = rel_display(args.eval, root) if args.eval else ""
    record = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "eval_reward",
        "generated_at": generated_at,
        "skill": skills[0] if len(skills) == 1 else "",
        "skill_group": skills if len(skills) != 1 else [],
        "fixture_set_id": fixture_set_id(eval_report, eval_ref),
        "eval_report_ref": eval_ref,
        "case_results": case_results,
        "components": components,
        "confidence_bp": confidence_bp,
        "coverage_bp": coverage_bp,
        "evidence": evidence,
        "explanations": explanations,
        "warnings": warnings,
        "penalties": penalties,
        "source_identifiers": {
            "audit": rel_display(args.audit, root) if args.audit else "",
            "eval": eval_ref,
            "fixture_set_id": fixture_set_id(eval_report, eval_ref),
        },
    }
    return finalize_record(record, root)


def fixture_set_id(eval_report: dict[str, Any] | None, eval_ref: str) -> str:
    if isinstance(eval_report, dict):
        for key in ("fixture_set_id", "fixtures"):
            if eval_report.get(key):
                return str(eval_report[key])
    return eval_ref or "unknown"


def proposal_files(candidate_dir: str, root: Path) -> tuple[list[Path], list[str]]:
    if not candidate_dir:
        return [], []
    path = Path(candidate_dir)
    guard = validate_input_path(path, root, "--candidate-dir")
    if not guard.ok:
        return [], guard.errors
    if path.is_file():
        return [path], []
    if not path.exists():
        return [], ["--candidate-dir does not exist"]
    return sorted(path.glob("**/*.json")), []


def proposal_fingerprint(proposal: dict[str, Any]) -> str:
    if proposal.get("fingerprint"):
        return str(proposal.get("fingerprint"))
    basis = {
        "schema_version": proposal.get("schema_version"),
        "skill_path": proposal.get("skill_path"),
        "baseline_sha256": proposal.get("baseline_sha256"),
        "edit_type": proposal.get("edit_type"),
        "target_section": proposal.get("target_section"),
        "patch": "\n".join(line.rstrip() for line in str(proposal.get("patch", "")).strip().splitlines()) + "\n",
        "source_gap_codes": sorted(proposal.get("source_gap_codes", [])),
    }
    return sha256_text(canonical_json(basis))


# changed_line_count imported from skillopt_common


def skill_name_from_path(skill_path: str) -> str:
    """Skill name as `skillopt_eval.py` keys its case results.

    Every auditable path is `<root>/<name>/{SKILL,README}.md`, so the parent
    directory is the name.
    """
    return Path(skill_path).parent.name


def scoped_eval_context(eval_results: list[dict[str, Any]], skill_path: str) -> tuple[int, int]:
    """Return (eval_quality_bp, case_count) for a proposal's target skill.

    Eval coverage is per skill: a proposal must never inherit the pass rate of
    unrelated skills. A skill with no held-out cases gets the same 2500 bp
    `eval_absent` floor the eval-level scorer applies, and its case count holds
    confidence and coverage down instead of borrowing the report's breadth.
    """
    target = skill_name_from_path(skill_path)
    cases = [row for row in eval_results if str(row.get("skill", "")) == target]
    if not cases:
        return 2500, 0
    passed = sum(1 for row in cases if row.get("passed"))
    return clamp((passed * 10000) // len(cases), 0, 10000), len(cases)


def legacy_rank(proposal: dict[str, Any]) -> list[Any]:
    gaps = set(str(g) for g in proposal.get("source_gap_codes") or [])
    skill_path = str(proposal.get("skill_path", ""))
    return [
        0 if gaps & LOW_RISK_GAPS else 1,
        0 if not SIDE_EFFECT_PATH_RE.search(skill_path) else 1,
        changed_line_count(str(proposal.get("patch", ""))),
        str(proposal.get("proposal_id", "")),
    ]


def lineage_indexes(accepted: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    accepted_fps = {str(row.get("fingerprint")) for row in accepted if row.get("fingerprint")}
    rejected_fps = {str(row.get("fingerprint")) for row in rejected if row.get("fingerprint")}
    return accepted_fps, rejected_fps


def proposal_components(proposal: dict[str, Any], eval_components: dict[str, int], eval_present: bool, accepted_fps: set[str], rejected_fps: set[str], raw_privacy_signal: bool = False) -> tuple[dict[str, int], list[str], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    explanations: list[str] = []
    warnings: list[dict[str, Any]] = []
    penalties: list[dict[str, Any]] = []
    evidence: list[str] = []
    gaps = set(str(g) for g in proposal.get("source_gap_codes") or [])
    patch_lines = changed_line_count(str(proposal.get("patch", "")))
    fp = proposal_fingerprint(proposal)
    components = dict(eval_components)
    components["contract_quality_bp"] = clamp(8200 - min(len(gaps), 6) * 450, 0, 10000)
    components["efficiency_bp"] = clamp(10000 - max(0, patch_lines - 4) * 350, 1000, 10000)
    components["stability_bp"] = clamp(9000 - (900 if str(proposal.get("edit_type")) in {"replace", "delete"} else 0), 0, 10000)
    components["safety_bp"] = min(components.get("safety_bp", 10000), 10000)
    components["runtime_risk_bp"] = min(components.get("runtime_risk_bp", 10000), 10000)
    if SIDE_EFFECT_PATH_RE.search(str(proposal.get("skill_path", ""))):
        components["runtime_risk_bp"] = min(components["runtime_risk_bp"], 2500)
        warnings.append({"code": "side_effect_path", "severity": "hard_gate", "message": "proposal touches a side-effect or runtime-like path"})
        penalties.append({"code": "side_effect_path", "component": "runtime_risk_bp", "bp": -7500})
    if raw_privacy_signal or contains_forbidden(proposal):
        components["safety_bp"] = 0
        warnings.append({"code": "privacy_signal", "severity": "hard_gate", "message": "proposal contains forbidden private path/token/webhook/mailbox-only content"})
        penalties.append({"code": "privacy_signal", "component": "safety_bp", "bp": -10000})
    if fp in rejected_fps:
        components["lineage_bp"] = -10000
        warnings.append({"code": "previously_rejected_fingerprint", "severity": "hard_gate", "message": "fingerprint appears in rejected lineage"})
        penalties.append({"code": "previously_rejected_fingerprint", "component": "lineage_bp", "bp": -10000})
    elif fp in accepted_fps:
        components["lineage_bp"] = 5000
        explanations.append("accepted fingerprint repeat gives zero novelty and no positive lineage reward")
        penalties.append({"code": "accepted_fingerprint_repeat", "component": "lineage_bp", "bp": -5000})
    else:
        components["lineage_bp"] = 7000
    if eval_present:
        evidence.append("eval_reward:component_context")
    else:
        warnings.append({"code": "eval_absent", "severity": "warning", "message": "target skill has no held-out eval cases"})
        # Backstop for callers passing unscoped components; scoped_eval_context already returns 2500.
        components["eval_quality_bp"] = min(components.get("eval_quality_bp", 0), 3500)
    explanations.extend([
        f"legacy rank tuple is {legacy_rank(proposal)}",
        "eval_quality component follows held-out case pass rate for this skill only",
        "contract_quality component reflects proposal gap scope",
        "safety component applies privacy and secret penalties",
        "stability component penalizes riskier edit operations",
        f"efficiency component reflects {patch_lines} non-empty patch line(s)",
        "lineage component is read-only and capped; reward cannot accept or apply proposals",
        "runtime_risk component penalizes side-effect-like paths",
    ])
    evidence.append(f"proposal:{str(proposal.get('proposal_id', 'unknown'))}")
    return ordered_components(components), explanations, warnings, penalties, evidence


def audit_content_hashes(audit_report: dict[str, Any] | None) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not isinstance(audit_report, dict):
        return hashes
    for row in audit_report.get("skills") or audit_report.get("records") or []:
        if isinstance(row, dict) and row.get("path") and row.get("content_sha256"):
            hashes[str(row["path"])] = str(row["content_sha256"])
    return hashes


def proposal_reward_records(args: argparse.Namespace, root: Path, eval_record: dict[str, Any] | None, eval_report: dict[str, Any] | None, audit_report: dict[str, Any] | None, generated_at: str, accepted_rows: list[dict[str, Any]], rejected_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    files, errors = proposal_files(args.candidate_dir, root)
    if errors:
        return [], errors
    accepted_fps, rejected_fps = lineage_indexes(accepted_rows, rejected_rows)
    global_eval_components = (eval_record or {}).get("components") or {key: 5000 for key in COMPONENT_KEYS}
    eval_results = extract_eval_results(eval_report)
    audit_hashes = audit_content_hashes(audit_report)
    records: list[dict[str, Any]] = []
    for path in files:
        try:
            raw_proposal = read_json(path)
            raw_privacy_signal = contains_forbidden(raw_proposal)
            proposal = sanitize_value(raw_proposal, root)
        except Exception as exc:  # noqa: BLE001
            records.append(invalid_proposal_record(path, root, generated_at, f"could not read proposal JSON: {exc}"))
            continue
        if not isinstance(proposal, dict):
            records.append(invalid_proposal_record(path, root, generated_at, "proposal JSON is not an object"))
            continue
        fp = proposal_fingerprint(proposal)
        skill_eval_quality, skill_case_count = scoped_eval_context(eval_results, str(proposal.get("skill_path", "")))
        scoped_components = dict(global_eval_components)
        scoped_components["eval_quality_bp"] = skill_eval_quality
        components, explanations, warnings, penalties, evidence = proposal_components(proposal, scoped_components, bool(skill_case_count), accepted_fps, rejected_fps, raw_privacy_signal)
        confidence_bp = int((eval_record or {}).get("confidence_bp", 4500)) if skill_case_count else 3500
        coverage_bp = int((eval_record or {}).get("coverage_bp", 3000)) if skill_case_count else 3000
        if skill_case_count:
            # Breadth is per skill too: a one-case skill must not borrow the
            # aggregate report's confidence. Mirrors the eval-level case term.
            coverage_bp = min(coverage_bp, clamp(2500 + min(skill_case_count, 8) * 750, 0, 10000))
            if skill_case_count < 3:
                confidence_bp = min(confidence_bp, 5500)
                warnings.append({"code": "small_fixture_set", "severity": "warning", "message": f"target skill has only {skill_case_count} held-out case(s)"})
        if len(proposal.get("source_gap_codes") or []) > 0:
            coverage_bp = min(10000, coverage_bp + 800)
        if not HEX64_RE.match(str(proposal.get("baseline_sha256", ""))):
            warnings.append({"code": "missing_or_invalid_baseline", "severity": "hard_gate", "message": "proposal baseline_sha256 is missing or invalid"})
        expected_hash = audit_hashes.get(str(proposal.get("skill_path", "")))
        if expected_hash and str(proposal.get("baseline_sha256", "")) != expected_hash:
            warnings.append({"code": "stale_baseline", "severity": "hard_gate", "message": "proposal baseline does not match audit content hash"})
            penalties.append({"code": "stale_baseline", "component": "stability_bp", "bp": -10000})
        gate = proposal.get("review_gate") if isinstance(proposal.get("review_gate"), dict) else {}
        if gate.get("automatic_accept") is not False or proposal.get("requires_human_review") is not True:
            warnings.append({"code": "unsafe_review_gate", "severity": "hard_gate", "message": "proposal must remain human-review gated and non-automatic"})
            penalties.append({"code": "unsafe_review_gate", "component": "safety_bp", "bp": -10000})
        if not str(proposal.get("proposal_id", "")).strip():
            warnings.append({"code": "missing_proposal_id", "severity": "hard_gate", "message": "proposal_id is required"})
        required_component_missing = any(key not in components for key in COMPONENT_KEYS)
        hard_gate = any(isinstance(w, dict) and w.get("severity") == "hard_gate" for w in warnings)
        eligible = confidence_bp >= 6000 and coverage_bp >= 5000 and not required_component_missing and not hard_gate
        if confidence_bp < 6000 or coverage_bp < 5000:
            penalties.append({"code": "low_confidence_or_coverage", "component": "eval_quality_bp", "bp": -1000})
        record = {
            "schema_version": SCHEMA_VERSION,
            "report_type": "proposal_reward",
            "generated_at": generated_at,
            "proposal_id": str(proposal.get("proposal_id", "")),
            "fingerprint": fp,
            "skill_path": rel_display(str(proposal.get("skill_path", "")), root),
            "baseline_sha256": str(proposal.get("baseline_sha256", "")),
            "candidate_ref": rel_display(path, root),
            "eval_reward_ref": eval_record.get("run_id") if eval_record else "",
            "legacy_rank": legacy_rank(proposal),
            "reward_rank_eligible": eligible,
            "rank_basis": "reward" if eligible else "legacy_rank",
            "hard_gate_passed": not hard_gate,
            "accepted": False,
            "approval_status": "rank_only" if eligible else "advisory",
            "components": components,
            "confidence_bp": confidence_bp,
            "coverage_bp": coverage_bp,
            "evidence": evidence,
            "explanations": explanations,
            "warnings": warnings,
            "penalties": penalties,
            "source_identifiers": {
                "proposal_id": str(proposal.get("proposal_id", "")),
                "fingerprint": fp,
                "candidate_ref": rel_display(path, root),
                "eval": rel_display(args.eval, root) if args.eval else "",
            },
        }
        records.append(finalize_record(record, root))
    records.sort(key=lambda r: (str(r.get("proposal_id", "")), str(r.get("fingerprint", "")), str(r.get("candidate_ref", ""))))
    return records, []


def invalid_proposal_record(path: Path, root: Path, generated_at: str, reason: str) -> dict[str, Any]:
    record = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "proposal_reward",
        "generated_at": generated_at,
        "proposal_id": "",
        "fingerprint": deterministic_id("invalid", {"candidate_ref": rel_display(path, root), "reason": reason}, 12),
        "skill_path": "",
        "baseline_sha256": "",
        "candidate_ref": rel_display(path, root),
        "eval_reward_ref": "",
        "legacy_rank": [1, 1, 1000000, rel_display(path, root)],
        "reward_rank_eligible": False,
        "components": {key: 0 if key in {"safety_bp", "runtime_risk_bp"} else 2500 for key in COMPONENT_KEYS},
        "confidence_bp": 0,
        "coverage_bp": 0,
        "evidence": [],
        "explanations": ["invalid proposal records are advisory diagnostics only"],
        "warnings": [{"code": "invalid_proposal", "severity": "hard_gate", "message": sanitize_text(reason, root)}],
        "penalties": [{"code": "invalid_proposal", "component": "safety_bp", "bp": -10000}],
        "source_identifiers": {"candidate_ref": rel_display(path, root), "reason": sanitize_text(reason, root)},
    }
    return finalize_record(record, root)


def make_report(args: argparse.Namespace) -> tuple[dict[str, Any] | None, list[str]]:
    root = Path(args.root).resolve()
    generated_at = args.as_of or now_iso()
    errors: list[str] = []
    audit_report, audit_errors = load_optional_json(args.audit, root, "--audit")
    eval_report, eval_errors = load_optional_json(args.eval, root, "--eval")
    accepted_rows, accepted_errors = load_jsonl(args.accepted_lineage, root, "--accepted-lineage")
    rejected_rows, rejected_errors = load_jsonl(args.rejected_buffer, root, "--rejected-buffer")
    errors.extend(audit_errors + eval_errors + accepted_errors + rejected_errors)
    if errors:
        return None, errors
    eval_record = eval_reward_record(args, root, audit_report if isinstance(audit_report, dict) else None, eval_report if isinstance(eval_report, dict) else None, generated_at)
    records = [eval_record]
    if args.candidate_dir:
        proposal_records, proposal_errors = proposal_reward_records(args, root, eval_record, eval_report if isinstance(eval_report, dict) else None, audit_report if isinstance(audit_report, dict) else None, generated_at, accepted_rows, rejected_rows)
        if proposal_errors:
            return None, proposal_errors
        records.extend(proposal_records)
    report_basis = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "record_run_ids": [record["run_id"] for record in records],
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "reward_report",
        "generated_at": generated_at,
        "run_id": deterministic_id("skillopt-reward", report_basis),
        "root": ".",
        "inputs": {
            "audit": rel_display(args.audit, root) if args.audit else "",
            "eval": rel_display(args.eval, root) if args.eval else "",
            "candidate_dir": rel_display(args.candidate_dir, root) if args.candidate_dir else "",
            "accepted_lineage": rel_display(args.accepted_lineage, root) if args.accepted_lineage else "",
            "rejected_buffer": rel_display(args.rejected_buffer, root) if args.rejected_buffer else "",
        },
        "policy": {
            "advisory_only": True,
            "automatic_accept": False,
            "mutates_skill_files": False,
            "mutates_runtime": False,
            "external_side_effects": False,
            "fallback_when_confidence_below_bp": 6000,
            "fallback_when_coverage_below_bp": 5000,
            "component_order": list(COMPONENT_KEYS),
            "weights_bp": WEIGHTS,
        },
        "records": records,
        "privacy_sanitized": True,
        "warnings": [],
    }
    sanitized = sanitize_value(report, root)
    if privacy_errors(sanitized):
        return None, ["reward report contains forbidden private path/token/webhook/mailbox-only content after sanitization"]
    return sanitized, []


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SkillOpt deterministic reward scorer")
    subparsers = parser.add_subparsers(dest="command")
    score = subparsers.add_parser("score", help="score eval and optional proposal candidates")
    add_score_args(score)
    add_score_args(parser)
    args = parser.parse_args(argv)
    if args.command not in (None, "score"):
        parser.error("unsupported command")
    return args


def add_score_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--audit", default="", help="skillopt-audit.v1 JSON report")
    parser.add_argument("--eval", default="", help="skillopt-eval.v1 JSON report")
    parser.add_argument("--candidate-dir", default="", help="proposal JSON file or directory for proposal_reward records")
    parser.add_argument("--accepted-lineage", default="", help="read-only accepted lineage JSONL")
    parser.add_argument("--rejected-buffer", default="", help="read-only rejected fingerprint JSONL")
    parser.add_argument("--out", default="", help="output JSON path under .omx/reports/skillopt or temp")
    parser.add_argument("--as-of", default="", help="fixed generated_at timestamp for deterministic byte-stable output")
    parser.add_argument("--root", default=".", help="repository root")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    root = Path(args.root).resolve()
    if args.out:
        guard = validate_report_output_path(Path(args.out), root, "--out")
        if not guard.ok:
            for error in guard.errors:
                print(f"FAIL: {error}", file=sys.stderr)
            return 1
    report, errors = make_report(args)
    if errors or report is None:
        print("reward validation: FAIL", file=sys.stderr)
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    if args.out:
        write_json(Path(args.out), report)
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
