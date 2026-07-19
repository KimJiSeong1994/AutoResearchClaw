from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillopt_reward.py"
AS_OF = "2026-06-30T00:00:00+09:00"
COMPONENT_KEYS = [
    "eval_quality_bp",
    "contract_quality_bp",
    "safety_bp",
    "stability_bp",
    "efficiency_bp",
    "lineage_bp",
    "runtime_risk_bp",
]
FORBIDDEN_SIGNALS = (
    "/Users/",
    "~",
    "Mobile Documents",
    "discord.com/api/webhooks",
    "sk-abcdefghijklmnopqrstuvwxyz",
    "private email body",
    "raw private body",
    "private body",
    "xoxb-private-token",
)

_TEMPS: list[tempfile.TemporaryDirectory[str]] = []


def write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def dump(path: Path, payload: Any) -> None:
    write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def sha256ish(char: str) -> str:
    return char * 64


def proposal(
    *,
    proposal_id: str,
    fingerprint: str,
    skill_path: str = ".codex/skills/example/SKILL.md",
    baseline_sha256: str | None = None,
    risk: str = "low",
    gap: str = "missing_verification",
    patch: str = "## Verification\n\n- Run the focused SkillOpt regression suite.\n",
    reviewer_verdict: str | None = None,
    automatic_accept: bool = False,
    changed_lines: int = 3,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "skillopt-proposal.v1",
        "proposal_id": proposal_id,
        "fingerprint": fingerprint,
        "skill": "example",
        "skill_path": skill_path,
        "baseline_sha256": baseline_sha256 or sha256ish("a"),
        "edit_type": "add",
        "target_section": "Verification",
        "rationale": "Add missing verification evidence.",
        "patch": patch,
        "evidence": ["audit:.omx/reports/skillopt/audit.json#missing_verification"],
        "risk": risk,
        "requires_human_review": True,
        "review_gate": {
            "reviewer_required": True,
            "critic_required": True,
            "automatic_accept": automatic_accept,
        },
        "source_gap_codes": [gap],
        "eval_summary": {"status": "PASS"},
        "privacy_sanitized": True,
        "diff_stats": {"changed_lines": changed_lines},
    }
    if reviewer_verdict is not None:
        payload["reviewer_verdict"] = reviewer_verdict
    return payload


def fixture_root(
    *,
    tiny_eval: bool = False,
    extra_candidates: list[dict[str, Any]] | None = None,
    eval_results_override: list[dict[str, Any]] | None = None,
) -> Path:
    tmp = tempfile.TemporaryDirectory()
    _TEMPS.append(tmp)
    root = Path(tmp.name)
    write(
        root / ".codex/skills/example/SKILL.md",
        """---
name: example
---
# Example

Use this skill for SkillOpt reward tests.
""",
    )
    audit = {
        "schema_version": "skillopt-audit.v1",
        "generated_at": "2026-06-29T00:00:00+09:00",
        "gap_definitions": {"missing_verification": {"risk": "low"}, "runtime_unmapped": {"risk": "medium"}},
        "skills": [
            {
                "name": "example",
                "path": ".codex/skills/example/SKILL.md",
                "content_sha256": sha256ish("a"),
                "dimensions": {
                    "input_contract": {"present": True},
                    "output_contract": {"present": True},
                    "workflow": {"present": True},
                    "safety_privacy": {"present": True},
                    "verification": {"present": False},
                },
                "gaps": [{"gap_code": "missing_verification"}],
            }
        ],
    }
    eval_results = [
        {"skill": "example", "case_id": "case-public-1", "passed": True, "details": {"actual": {"verdict": "accept"}}},
        {"skill": "example", "case_id": "case-public-2", "passed": True, "details": {"actual": {"verdict": "reject"}}},
        {"skill": "example", "case_id": "case-public-3", "passed": True, "details": {"actual": {"verdict": "needs_review"}}},
        {"skill": "example", "case_id": "case-public-4", "passed": True, "details": {"actual": {"verdict": "accept"}}},
    ]
    if tiny_eval:
        eval_results = eval_results[:1]
    if eval_results_override is not None:
        eval_results = eval_results_override
    passed_count = sum(1 for row in eval_results if row.get("passed"))
    eval_report = {
        "schema_version": "skillopt-eval.v1",
        "generated_at": "2026-06-29T00:00:00+09:00",
        "summary": {
            "total": len(eval_results),
            "passed": passed_count,
            "failed": len(eval_results) - passed_count,
            "status": "PASS" if passed_count == len(eval_results) else "FAIL",
        },
        "results": eval_results,
        "acceptance_policy": {"automatic_accept": False, "requires_reviewer_gate": True},
    }
    dump(root / ".omx/reports/skillopt/audit.json", audit)
    dump(root / ".omx/reports/skillopt/eval.json", eval_report)

    candidate_dir = root / ".omx/reports/skillopt/patch-candidates"
    candidates = [
        proposal(proposal_id="skillopt-example-safe", fingerprint=sha256ish("1"), changed_lines=3),
        proposal(proposal_id="skillopt-example-rejected", fingerprint=sha256ish("2"), changed_lines=2),
        proposal(proposal_id="skillopt-example-stale", fingerprint=sha256ish("3"), baseline_sha256=sha256ish("b"), changed_lines=1),
        proposal(
            proposal_id="skillopt-example-privacy",
            fingerprint=sha256ish("4"),
            patch=(
                "## Verification\n\n"
                "- Do not echo /Users/jiseong/Mobile Documents/private.md.\n"
                "- Redact https://discord.com/api/webhooks/123/secret and sk-abcdefghijklmnopqrstuvwxyz.\n"
                "- Ignore private email body, raw private body, private body, and xoxb-private-token.\n"
            ),
            changed_lines=1,
        ),
        proposal(
            proposal_id="skillopt-example-unsafe-auto",
            fingerprint=sha256ish("5"),
            risk="high",
            gap="runtime_unmapped",
            automatic_accept=True,
            changed_lines=1,
        ),
    ]
    candidates.extend(extra_candidates or [])
    for item in candidates:
        dump(candidate_dir / item["skill"] / f'{item["proposal_id"]}.json', item)

    accepted = {
        "schema_version": "skillopt-accepted-lineage.v1",
        "proposal_id": "already-accepted",
        "fingerprint": sha256ish("9"),
        "skill": "example",
        "skill_path": ".codex/skills/example/SKILL.md",
        "before_sha256": sha256ish("8"),
        "after_sha256": sha256ish("7"),
        "reviewer_verdict": "APPROVE",
        "critic_verdict": "APPROVE",
        "accepted_at": "2026-06-28T00:00:00+09:00",
    }
    rejected = {
        "schema_version": "skillopt-rejected-edit.v1",
        "proposal_id": "skillopt-example-rejected",
        "fingerprint": sha256ish("2"),
        "reason": "weak evidence",
        "reviewer": "critic",
        "rejected_at": "2026-06-28T00:00:00+09:00",
    }
    write(root / ".omx/reports/skillopt/accepted-lineage.jsonl", json.dumps(accepted, sort_keys=True) + "\n")
    write(root / ".omx/reports/skillopt/rejected-edits.jsonl", json.dumps(rejected, sort_keys=True) + "\n")
    return root


def run_score(root: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "score",
            "--audit",
            str(root / ".omx/reports/skillopt/audit.json"),
            "--eval",
            str(root / ".omx/reports/skillopt/eval.json"),
            "--candidate-dir",
            str(root / ".omx/reports/skillopt/patch-candidates"),
            "--accepted-lineage",
            str(root / ".omx/reports/skillopt/accepted-lineage.jsonl"),
            "--rejected-buffer",
            str(root / ".omx/reports/skillopt/rejected-edits.jsonl"),
            "--out",
            str(out),
            "--as-of",
            AS_OF,
            "--root",
            str(root),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def load_report(root: Path, name: str = "reward.json") -> dict[str, Any]:
    out = root / ".omx/reports/skillopt" / name
    proc = run_score(root, out)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def records_by_type(report: dict[str, Any], report_type: str) -> list[dict[str, Any]]:
    return [record for record in report["records"] if record["report_type"] == report_type]


def assert_reward_record_schema(record: dict[str, Any]) -> None:
    assert record["schema_version"] == "skillopt-reward.v1"
    assert record["report_type"] in {"eval_reward", "proposal_reward"}
    assert isinstance(record["run_id"], str) and record["run_id"]
    assert record["generated_at"] == AS_OF
    assert isinstance(record["score_bp"], int) and -10000 <= record["score_bp"] <= 10000
    assert isinstance(record["confidence_bp"], int) and 0 <= record["confidence_bp"] <= 10000
    assert isinstance(record["coverage_bp"], int) and 0 <= record["coverage_bp"] <= 10000
    assert list(record["components"].keys()) == COMPONENT_KEYS
    assert all(isinstance(record["components"][key], int) for key in COMPONENT_KEYS)
    assert isinstance(record["evidence"], list)
    assert isinstance(record["explanations"], list) and record["explanations"]
    assert isinstance(record["warnings"], list)
    assert isinstance(record["penalties"], list)
    assert record["privacy_sanitized"] is True


def test_reward_score_emits_schema_valid_eval_and_proposal_records() -> None:
    root = fixture_root()
    report = load_report(root)

    assert report["schema_version"] == "skillopt-reward.v1"
    assert report["generated_at"] == AS_OF
    assert {record["report_type"] for record in report["records"]} == {"eval_reward", "proposal_reward"}
    assert records_by_type(report, "eval_reward")
    assert records_by_type(report, "proposal_reward")
    for record in report["records"]:
        assert_reward_record_schema(record)

    eval_record = records_by_type(report, "eval_reward")[0]
    assert "proposal_id" not in eval_record
    assert eval_record["skill"] == "example"
    assert eval_record["fixture_set_id"]
    assert eval_record["eval_report_ref"]
    assert eval_record["case_results"]

    proposal_record = next(record for record in records_by_type(report, "proposal_reward") if record["proposal_id"] == "skillopt-example-safe")
    for key in ("proposal_id", "fingerprint", "skill_path", "baseline_sha256", "candidate_ref", "legacy_rank", "reward_rank_eligible"):
        assert key in proposal_record


def test_reward_score_is_byte_deterministic_for_fixed_as_of() -> None:
    root = fixture_root()
    out = root / ".omx/reports/skillopt/reward.json"

    proc1 = run_score(root, out)
    first_bytes = out.read_bytes() if out.exists() else b""
    proc2 = run_score(root, out)
    second_bytes = out.read_bytes() if out.exists() else b""

    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    assert first_bytes == second_bytes
    report = json.loads(first_bytes.decode("utf-8"))
    assert all("uuid" not in record["run_id"].lower() for record in report["records"])


def test_reward_scalar_is_consistent_with_components_and_canonical_keys() -> None:
    root = fixture_root()
    report = load_report(root)
    for record in report["records"]:
        assert list(record["components"].keys()) == COMPONENT_KEYS
        weights = record["policy"]["weights_bp"]
        assert list(weights.keys()) == COMPONENT_KEYS
        weighted = sum(record["components"][key] * weights[key] for key in COMPONENT_KEYS)
        expected_score = int(round(weighted / 10000))
        assert record["score_bp"] == expected_score
        influential = [key for key, value in record["components"].items() if value != 0]
        explanation = " ".join(record["explanations"])
        assert any(key.replace("_bp", "") in explanation for key in influential)


def test_reward_privacy_rejects_private_paths_webhooks_tokens_and_private_bodies_without_echo() -> None:
    root = fixture_root()
    report = load_report(root)
    serialized = json.dumps(report, ensure_ascii=False)
    for signal in FORBIDDEN_SIGNALS:
        assert signal not in serialized

    privacy_record = next(record for record in records_by_type(report, "proposal_reward") if record["proposal_id"] == "skillopt-example-privacy")
    assert privacy_record["reward_rank_eligible"] is False
    assert any(warning.get("severity") == "hard_gate" and warning.get("code") == "privacy_signal" for warning in privacy_record["warnings"])
    assert privacy_record.get("hard_gate_passed") is False


def test_low_coverage_confidence_falls_back_to_legacy_rank() -> None:
    root = fixture_root(tiny_eval=True)
    report = load_report(root)
    safe_record = next(record for record in records_by_type(report, "proposal_reward") if record["proposal_id"] == "skillopt-example-safe")

    assert safe_record["coverage_bp"] < 5000 or safe_record["confidence_bp"] < 6000
    assert safe_record["reward_rank_eligible"] is False
    assert safe_record["legacy_rank"]
    assert safe_record["rank_basis"] == "legacy_rank"
    assert any(penalty.get("code") == "low_confidence_or_coverage" for penalty in safe_record["penalties"])


def test_lineage_is_read_only_and_rejected_fingerprint_gets_penalty() -> None:
    root = fixture_root()
    accepted_path = root / ".omx/reports/skillopt/accepted-lineage.jsonl"
    rejected_path = root / ".omx/reports/skillopt/rejected-edits.jsonl"
    before_accepted = accepted_path.read_bytes()
    before_rejected = rejected_path.read_bytes()

    report = load_report(root)

    assert accepted_path.read_bytes() == before_accepted
    assert rejected_path.read_bytes() == before_rejected
    rejected_record = next(record for record in records_by_type(report, "proposal_reward") if record["proposal_id"] == "skillopt-example-rejected")
    assert rejected_record["reward_rank_eligible"] is False
    assert rejected_record["components"]["lineage_bp"] < 0
    assert any(penalty.get("code") == "previously_rejected_fingerprint" for penalty in rejected_record["penalties"])


def test_reward_is_advisory_and_cannot_mark_unsafe_stale_or_private_candidates_accepted() -> None:
    root = fixture_root()
    report = load_report(root)
    by_id = {record["proposal_id"]: record for record in records_by_type(report, "proposal_reward")}

    for proposal_id, expected_code in {
        "skillopt-example-stale": "stale_baseline",
        "skillopt-example-privacy": "privacy_signal",
        "skillopt-example-unsafe-auto": "unsafe_review_gate",
        "skillopt-example-rejected": "previously_rejected_fingerprint",
    }.items():
        record = by_id[proposal_id]
        assert record.get("accepted") is not True
        assert record.get("approval_status") != "accepted"
        assert record["reward_rank_eligible"] is False
        assert record.get("hard_gate_passed") is False
        warning_and_penalty_codes = {item.get("code") for item in [*record["warnings"], *record["penalties"]]}
        assert expected_code in warning_and_penalty_codes

    safe_record = by_id["skillopt-example-safe"]
    assert safe_record.get("accepted") is not True
    assert safe_record.get("approval_status", "advisory") in {"advisory", "rank_only", "pending"}


def test_skill_name_from_path_handles_codex_and_runtime_layouts() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import skillopt_reward
    finally:
        sys.path.pop(0)

    assert skillopt_reward.skill_name_from_path(".codex/skills/academic-technical-filter/SKILL.md") == "academic-technical-filter"
    assert skillopt_reward.skill_name_from_path("skills/discord-openclaw-bridge/SKILL.md") == "discord-openclaw-bridge"
    assert skillopt_reward.skill_name_from_path("skills/discord-openclaw-bridge/README.md") == "discord-openclaw-bridge"
    assert skillopt_reward.skill_name_from_path("skills/paper-recommender/SKILL.md") == "paper-recommender"


def test_proposal_targeting_skill_without_fixtures_does_not_inherit_eval_quality() -> None:
    """A proposal must never borrow the pass rate of an unrelated skill.

    Regression: `components = dict(eval_components)` gave every proposal the
    global eval score, so skills/discord-openclaw-bridge (zero fixtures) scored
    eval_quality_bp=10000 off three unrelated skills passing 8/8.
    """
    uncovered = proposal(
        proposal_id="skillopt-uncovered-safe",
        fingerprint=sha256ish("a"),
        skill_path="skills/uncovered-skill/SKILL.md",
        changed_lines=3,
    )
    uncovered["skill"] = "uncovered-skill"
    root = fixture_root(extra_candidates=[uncovered])
    report = load_report(root)
    by_id = {record["proposal_id"]: record for record in records_by_type(report, "proposal_reward")}

    record = by_id["skillopt-uncovered-safe"]
    assert record["components"]["eval_quality_bp"] == 2500
    assert record["confidence_bp"] == 3500
    assert record["coverage_bp"] <= 3800
    assert any(warning.get("code") == "eval_absent" for warning in record["warnings"])

    covered = by_id["skillopt-example-safe"]
    assert covered["components"]["eval_quality_bp"] == 10000
    assert record["score_bp"] < covered["score_bp"]


def test_proposal_eval_quality_follows_per_skill_pass_rate() -> None:
    other = proposal(
        proposal_id="skillopt-other-safe",
        fingerprint=sha256ish("b"),
        skill_path=".codex/skills/other/SKILL.md",
        changed_lines=3,
    )
    other["skill"] = "other"
    root = fixture_root(
        extra_candidates=[other],
        eval_results_override=[
            {"skill": "example", "case_id": "e1", "passed": True, "details": {}},
            {"skill": "example", "case_id": "e2", "passed": True, "details": {}},
            {"skill": "example", "case_id": "e3", "passed": True, "details": {}},
            {"skill": "example", "case_id": "e4", "passed": False, "details": {}},
            {"skill": "other", "case_id": "o1", "passed": True, "details": {}},
            {"skill": "other", "case_id": "o2", "passed": True, "details": {}},
        ],
    )
    report = load_report(root)
    by_id = {record["proposal_id"]: record for record in records_by_type(report, "proposal_reward")}

    assert by_id["skillopt-example-safe"]["components"]["eval_quality_bp"] == 7500
    assert by_id["skillopt-other-safe"]["components"]["eval_quality_bp"] == 10000
