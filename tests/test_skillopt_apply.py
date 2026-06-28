from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillopt_apply.py"
PROPOSE_SCRIPT = ROOT / "scripts" / "skillopt_propose.py"
_TEMPS: list[tempfile.TemporaryDirectory[str]] = []


def load_propose() -> Any:
    spec = importlib.util.spec_from_file_location("skillopt_propose", PROPOSE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def fixture_root() -> Path:
    tmp = tempfile.TemporaryDirectory()
    _TEMPS.append(tmp)
    root = Path(tmp.name)
    write(
        root / ".codex/skills/example/SKILL.md",
        """---
name: example
---
# Example

Use this skill for tests.

## Verification

- Existing check.

## Output contract

Return evidence.
""",
    )
    return root


def eval_report(status: str = "PASS", failed: int = 0) -> dict[str, Any]:
    return {
        "schema_version": "skillopt-eval.v1",
        "summary": {"total": 1, "passed": 1 if failed == 0 else 0, "failed": failed, "status": status},
    }


def proposal(root: Path, gap: str = "missing_input_contract", edit_type: str = "add", patch: str | None = None) -> dict[str, Any]:
    mod = load_propose()
    skill_path = ".codex/skills/example/SKILL.md"
    skill_text = (root / skill_path).read_text(encoding="utf-8")
    payload = {
        "schema_version": "skillopt-proposal.v1",
        "proposal_id": "",
        "skill": "example",
        "skill_path": skill_path,
        "baseline_sha256": mod.sha256_text(skill_text),
        "edit_type": edit_type,
        "target_section": "Input contract" if edit_type == "add" else "Output contract",
        "rationale": "Improve contract.",
        "patch": patch or "## Input contract\n\n- Identify user request and missing facts.\n",
        "evidence": ["audit:.omx/reports/skillopt/audit.json#gap"],
        "risk": "low",
        "requires_human_review": True,
        "review_gate": {"reviewer_required": True, "critic_required": True, "automatic_accept": False},
        "source_gap_codes": [gap],
        "eval_summary": {"status": "PASS"},
        "privacy_sanitized": True,
    }
    fp = mod.proposal_fingerprint(payload)
    payload["fingerprint"] = fp
    payload["proposal_id"] = f"skillopt-example-{fp[:12]}"
    return payload


def write_proposal(root: Path, payload: dict[str, Any], name: str = "proposal.json") -> Path:
    path = root / ".omx/reports/skillopt/patch-candidates/example" / name
    write(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def select(root: Path) -> tuple[Path, subprocess.CompletedProcess[str]]:
    out = root / ".omx/reports/skillopt/apply-runs/selection.json"
    proc = run(root, "select", "--candidate-dir", str(root / ".omx/reports/skillopt/patch-candidates"), "--out", str(out), "--root", str(root))
    return out, proc


def dry_run(root: Path, prop_path: Path, selection_path: Path, name: str = "dry.json") -> tuple[Path, subprocess.CompletedProcess[str]]:
    out = root / ".omx/reports/skillopt/apply-runs" / name
    proc = run(root, "dry-run", str(prop_path), "--selection-report", str(selection_path), "--out", str(out), "--root", str(root))
    return out, proc


def test_select_records_deterministic_rationale_and_excludes_unsafe() -> None:
    root = fixture_root()
    good = proposal(root, "missing_input_contract")
    runtime = proposal(root, "runtime_unmapped")
    delete = proposal(root, "missing_verification", "delete", "")
    rejected = proposal(root, "weak_output_contract")
    rejected["reviewer_verdict"] = "REJECT"
    write_proposal(root, runtime, "2-runtime.json")
    write_proposal(root, delete, "3-delete.json")
    write_proposal(root, rejected, "4-rejected.json")
    good_path = write_proposal(root, good, "1-good.json")

    out1, proc1 = select(root)
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    first = json.loads(out1.read_text(encoding="utf-8"))
    out2, proc2 = select(root)
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    second = json.loads(out2.read_text(encoding="utf-8"))

    assert first["chosen"]["proposal_id"] == good["proposal_id"]
    assert second["chosen"]["proposal_id"] == good["proposal_id"]
    assert first["chosen"]["fingerprint"] == second["chosen"]["fingerprint"]
    assert first["chosen"]["approval_status"] == "pending"
    assert first["chosen"]["path"] == good_path.as_posix()
    reasons = " ".join(item["reason"] for item in first["rejected"])
    assert "runtime_unmapped" in reasons
    assert "delete" in reasons
    assert "rejection" in reasons


def test_dry_run_requires_matching_selection_and_does_not_mutate() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    before = (root / payload["skill_path"]).read_text(encoding="utf-8")

    dry_out, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert (root / payload["skill_path"]).read_text(encoding="utf-8") == before
    report = json.loads(dry_out.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["after_sha256"] != report["before_sha256"]

    bad_selection = json.loads(selection_path.read_text(encoding="utf-8"))
    bad_selection["chosen"]["fingerprint"] = "b" * 64
    bad_path = root / ".omx/reports/skillopt/apply-runs/bad-selection.json"
    write(bad_path, json.dumps(bad_selection))
    bad = run(root, "dry-run", str(prop_path), "--selection-report", str(bad_path), "--out", str(root / ".omx/reports/skillopt/apply-runs/bad.json"), "--root", str(root))
    assert bad.returncode != 0
    assert "selection-report" in bad.stderr


def test_apply_requires_approvals_and_appends_lineage_after_success() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry_path, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    eval_before = root / ".omx/reports/skillopt/eval-before.json"
    eval_after = root / ".omx/reports/skillopt/eval-after.json"
    write(eval_before, json.dumps(eval_report()))
    write(eval_after, json.dumps(eval_report()))
    lineage = root / ".omx/reports/skillopt/accepted-lineage.jsonl"

    missing = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(dry_path),
        "--reviewer-verdict",
        "PENDING",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(root / ".omx/reports/skillopt/apply-runs/fail.json"),
        "--root",
        str(root),
    )
    assert missing.returncode != 0
    assert not lineage.exists()

    ok = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(dry_path),
        "--reviewer-verdict",
        "APPROVE",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(root / ".omx/reports/skillopt/apply-runs/apply.json"),
        "--root",
        str(root),
        "--as-of",
        "2026-06-28T00:00:00Z",
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr
    text = (root / payload["skill_path"]).read_text(encoding="utf-8")
    assert "Identify user request" in text
    records = [json.loads(line) for line in lineage.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["proposal_id"] == payload["proposal_id"]
    assert records[0]["reviewer_verdict"] == "APPROVE"
    assert records[0]["critic_verdict"] == "APPROVE"


def test_apply_requires_matching_dry_run_report() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry_path, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    report = json.loads(dry_path.read_text(encoding="utf-8"))
    report["fingerprint"] = "b" * 64
    bad_dry = root / ".omx/reports/skillopt/apply-runs/bad-dry.json"
    write(bad_dry, json.dumps(report))
    eval_before = root / ".omx/reports/skillopt/eval-before.json"
    eval_after = root / ".omx/reports/skillopt/eval-after.json"
    write(eval_before, json.dumps(eval_report()))
    write(eval_after, json.dumps(eval_report()))
    lineage = root / ".omx/reports/skillopt/accepted-lineage.jsonl"
    before = (root / payload["skill_path"]).read_text(encoding="utf-8")

    proc = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(bad_dry),
        "--reviewer-verdict",
        "APPROVE",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(root / ".omx/reports/skillopt/apply-runs/apply.json"),
        "--root",
        str(root),
    )
    assert proc.returncode != 0
    assert "dry-run report fingerprint mismatch" in proc.stderr
    assert (root / payload["skill_path"]).read_text(encoding="utf-8") == before
    assert not lineage.exists()


def test_failed_eval_after_rolls_back_and_lineage_is_unchanged() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry_path, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    eval_before = root / ".omx/reports/skillopt/eval-before.json"
    eval_after = root / ".omx/reports/skillopt/eval-after.json"
    write(eval_before, json.dumps(eval_report()))
    write(eval_after, json.dumps(eval_report("FAIL", failed=1)))
    lineage = root / ".omx/reports/skillopt/accepted-lineage.jsonl"
    write(lineage, '{"existing": true}\n')
    before = (root / payload["skill_path"]).read_text(encoding="utf-8")

    proc = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(dry_path),
        "--reviewer-verdict",
        "APPROVE",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(root / ".omx/reports/skillopt/apply-runs/fail-eval.json"),
        "--root",
        str(root),
    )
    assert proc.returncode != 0
    assert (root / payload["skill_path"]).read_text(encoding="utf-8") == before
    assert lineage.read_text(encoding="utf-8") == '{"existing": true}\n'


def test_simulated_failure_before_lineage_rolls_back_without_record() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry_path, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    eval_before = root / ".omx/reports/skillopt/eval-before.json"
    eval_after = root / ".omx/reports/skillopt/eval-after.json"
    write(eval_before, json.dumps(eval_report()))
    write(eval_after, json.dumps(eval_report()))
    lineage = root / ".omx/reports/skillopt/accepted-lineage.jsonl"
    before = (root / payload["skill_path"]).read_text(encoding="utf-8")

    proc = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(dry_path),
        "--reviewer-verdict",
        "APPROVE",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(root / ".omx/reports/skillopt/apply-runs/simulated.json"),
        "--root",
        str(root),
        "--simulate-fail-before-lineage",
    )
    assert proc.returncode != 0
    assert (root / payload["skill_path"]).read_text(encoding="utf-8") == before
    assert not lineage.exists()


def test_reject_appends_sanitized_rejected_record() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    buffer = root / ".omx/reports/skillopt/rejected-edits.jsonl"
    proc = run(root, "reject", str(prop_path), "--reason", "too broad", "--buffer", str(buffer), "--root", str(root))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    record = json.loads(buffer.read_text(encoding="utf-8"))
    assert record["schema_version"] == "skillopt-rejected-edit.v1"
    assert record["proposal_id"] == payload["proposal_id"]


def test_protected_out_path_is_rejected_without_report_write() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    bad_out = root / "runtime/forbidden-report.json"
    proc = run(root, "dry-run", str(prop_path), "--selection-report", str(selection_path), "--out", str(bad_out), "--root", str(root))
    assert proc.returncode != 0
    assert not bad_out.exists()


def test_apply_rejects_non_skill_target_even_with_matching_selection() -> None:
    root = fixture_root()
    write(root / "docs/not-a-skill.md", "# Not a skill\n")
    payload = proposal(root)
    payload["skill_path"] = "docs/not-a-skill.md"
    payload["baseline_sha256"] = load_propose().sha256_text("# Not a skill\n")
    fp = load_propose().proposal_fingerprint(payload)
    payload["fingerprint"] = fp
    payload["proposal_id"] = f"skillopt-example-{fp[:12]}"
    prop_path = write_proposal(root, payload)
    selection = {
        "schema_version": "skillopt-selection.v1",
        "chosen": {"proposal_id": payload["proposal_id"], "fingerprint": payload["fingerprint"]},
    }
    selection_path = root / ".omx/reports/skillopt/apply-runs/selection.json"
    write(selection_path, json.dumps(selection))
    before = (root / "docs/not-a-skill.md").read_text(encoding="utf-8")
    proc = run(root, "dry-run", str(prop_path), "--selection-report", str(selection_path), "--out", str(root / ".omx/reports/skillopt/apply-runs/dry.json"), "--root", str(root))
    assert proc.returncode != 0
    assert "approved skill surface" in proc.stderr
    assert (root / "docs/not-a-skill.md").read_text(encoding="utf-8") == before


def test_selection_report_privacy_is_rejected_before_report_copy() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selection["chosen"]["leak"] = "/Users/private/file.md"
    write(selection_path, json.dumps(selection))
    out = root / ".omx/reports/skillopt/apply-runs/dry.json"
    proc = run(root, "dry-run", str(prop_path), "--selection-report", str(selection_path), "--out", str(out), "--root", str(root))
    assert proc.returncode != 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert "/Users/private" not in json.dumps(report)


def test_lineage_append_failure_rolls_back_skill() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry_path, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    eval_before = root / ".omx/reports/skillopt/eval-before.json"
    eval_after = root / ".omx/reports/skillopt/eval-after.json"
    write(eval_before, json.dumps(eval_report()))
    write(eval_after, json.dumps(eval_report()))
    lineage = root / ".omx/reports/skillopt/accepted-lineage.jsonl"
    lineage.mkdir(parents=True)
    before = (root / payload["skill_path"]).read_text(encoding="utf-8")
    proc = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(dry_path),
        "--reviewer-verdict",
        "APPROVE",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(root / ".omx/reports/skillopt/apply-runs/lineage-fail.json"),
        "--root",
        str(root),
    )
    assert proc.returncode != 0
    assert (root / payload["skill_path"]).read_text(encoding="utf-8") == before


def test_skill_path_traversal_cannot_bypass_allowlist() -> None:
    root = fixture_root()
    write(root / "README.md", "# Repo readme\n")
    write(root / "skills/foo/SKILL.md", "# Skill shell\n")
    payload = proposal(root)
    payload["skill_path"] = "skills/foo/../../README.md"
    payload["baseline_sha256"] = load_propose().sha256_text("# Repo readme\n")
    fp = load_propose().proposal_fingerprint(payload)
    payload["fingerprint"] = fp
    payload["proposal_id"] = f"skillopt-example-{fp[:12]}"
    prop_path = write_proposal(root, payload)
    selection = {
        "schema_version": "skillopt-selection.v1",
        "chosen": {"proposal_id": payload["proposal_id"], "fingerprint": payload["fingerprint"]},
    }
    selection_path = root / ".omx/reports/skillopt/apply-runs/traversal-selection.json"
    write(selection_path, json.dumps(selection))
    proc = run(root, "dry-run", str(prop_path), "--selection-report", str(selection_path), "--out", str(root / ".omx/reports/skillopt/apply-runs/traversal.json"), "--root", str(root))
    assert proc.returncode != 0
    assert "traversal" in proc.stderr or "approved skill surface" in proc.stderr
    assert (root / "README.md").read_text(encoding="utf-8") == "# Repo readme\n"


def test_skill_path_dot_and_empty_segments_are_rejected() -> None:
    root = fixture_root()
    write(root / "skills/foo/SKILL.md", "# Skill shell\n")
    for raw_path in ("skills/foo/./SKILL.md", "skills//foo/SKILL.md"):
        payload = proposal(root)
        payload["skill_path"] = raw_path
        payload["baseline_sha256"] = load_propose().sha256_text("# Skill shell\n")
        fp = load_propose().proposal_fingerprint(payload)
        payload["fingerprint"] = fp
        payload["proposal_id"] = f"skillopt-example-{fp[:12]}"
        prop_path = write_proposal(root, payload, f"{fp[:8]}.json")
        selection = {
            "schema_version": "skillopt-selection.v1",
            "chosen": {"proposal_id": payload["proposal_id"], "fingerprint": payload["fingerprint"]},
        }
        selection_path = root / f".omx/reports/skillopt/apply-runs/{fp[:8]}-selection.json"
        write(selection_path, json.dumps(selection))
        proc = run(root, "dry-run", str(prop_path), "--selection-report", str(selection_path), "--out", str(root / f".omx/reports/skillopt/apply-runs/{fp[:8]}.json"), "--root", str(root))
        assert proc.returncode != 0
        assert "empty path segments" in proc.stderr or "dot" in proc.stderr


def test_apply_report_write_failure_rolls_back_skill_and_lineage() -> None:
    root = fixture_root()
    payload = proposal(root)
    prop_path = write_proposal(root, payload)
    selection_path, proc = select(root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry_path, dry = dry_run(root, prop_path, selection_path)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    eval_before = root / ".omx/reports/skillopt/eval-before.json"
    eval_after = root / ".omx/reports/skillopt/eval-after.json"
    write(eval_before, json.dumps(eval_report()))
    write(eval_after, json.dumps(eval_report()))
    lineage = root / ".omx/reports/skillopt/accepted-lineage.jsonl"
    write(lineage, '{"existing": true}\n')
    out_dir = root / ".omx/reports/skillopt/apply-runs/report-dir.json"
    out_dir.mkdir(parents=True)
    before = (root / payload["skill_path"]).read_text(encoding="utf-8")
    proc = run(
        root,
        "apply",
        str(prop_path),
        "--selection-report",
        str(selection_path),
        "--dry-run-report",
        str(dry_path),
        "--reviewer-verdict",
        "APPROVE",
        "--critic-verdict",
        "APPROVE",
        "--eval-before",
        str(eval_before),
        "--eval-after",
        str(eval_after),
        "--lineage",
        str(lineage),
        "--out",
        str(out_dir),
        "--root",
        str(root),
    )
    assert proc.returncode != 0
    assert (root / payload["skill_path"]).read_text(encoding="utf-8") == before
    assert lineage.read_text(encoding="utf-8") == '{"existing": true}\n'
