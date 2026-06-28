from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillopt_propose.py"

_TEMPS: list[tempfile.TemporaryDirectory[str]] = []


def load_propose() -> Any:
    spec = importlib.util.spec_from_file_location("skillopt_propose", SCRIPT)
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
    skill_text = """---
name: example
---
# Example

Use this skill for tests.
"""
    write(root / ".codex/skills/example/SKILL.md", skill_text)
    content_hash = load_propose().sha256_text(skill_text)
    audit = {
        "schema_version": "skillopt-audit.v1",
        "generated_at": "2026-06-27T00:00:00+09:00",
        "skills": [
            {
                "name": "example",
                "path": ".codex/skills/example/SKILL.md",
                "content_sha256": content_hash,
                "gaps": [
                    {"gap_code": "missing_verification"},
                    {"gap_code": "missing_verification"},
                    {"gap_code": "runtime_unmapped"},
                ],
            }
        ],
    }
    eval_report = {
        "schema_version": "skillopt-eval.v1",
        "generated_at": "2026-06-27T00:00:00+09:00",
        "summary": {"total": 1, "passed": 1, "failed": 0, "status": "PASS"},
    }
    write(root / ".omx/reports/skillopt/audit.json", json.dumps(audit, ensure_ascii=False))
    write(root / ".omx/reports/skillopt/eval.json", json.dumps(eval_report, ensure_ascii=False))
    return root


def valid_proposal(edit_type: str = "add") -> dict[str, Any]:
    mod = load_propose()
    proposal = {
        "schema_version": "skillopt-proposal.v1",
        "proposal_id": "",
        "skill": "example",
        "skill_path": ".codex/skills/example/SKILL.md",
        "baseline_sha256": "a" * 64,
        "edit_type": edit_type,
        "target_section": "Verification",
        "rationale": "Add verification.",
        "patch": "## Verification\n\n- Run tests.\n",
        "evidence": ["audit:.omx/reports/skillopt/audit.json#missing_verification"],
        "risk": "low",
        "requires_human_review": True,
        "review_gate": {"reviewer_required": True, "critic_required": True, "automatic_accept": False},
        "source_gap_codes": ["missing_verification"],
        "eval_summary": {"status": "PASS"},
        "privacy_sanitized": True,
    }
    fp = mod.proposal_fingerprint(proposal)
    proposal["fingerprint"] = fp
    proposal["proposal_id"] = f"skillopt-example-{fp[:12]}"
    return proposal


def run_generate(root: Path, out_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--audit",
            str(root / ".omx/reports/skillopt/audit.json"),
            "--eval",
            str(root / ".omx/reports/skillopt/eval.json"),
            "--out-dir",
            str(out_dir),
            "--as-of",
            "2026-06-27T00:00:00+09:00",
            *extra,
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def run_generate_without_as_of(root: Path, out_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--root",
            str(root),
            "--audit",
            str(root / ".omx/reports/skillopt/audit.json"),
            "--eval",
            str(root / ".omx/reports/skillopt/eval.json"),
            "--out-dir",
            str(out_dir),
            *extra,
        ],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def proposal_files(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob("*/*.json"))


def test_valid_proposal_schema_accepts_bounded_edit_types() -> None:
    mod = load_propose()
    for edit_type in ("add", "delete", "replace"):
        result = mod.validate_proposal(valid_proposal(edit_type))
        assert result.ok, result.errors


def test_proposal_schema_rejects_invalid_gate_hash_risk_edit_and_privacy() -> None:
    mod = load_propose()
    cases = []
    p = valid_proposal(); p["requires_human_review"] = False; cases.append(p)
    p = valid_proposal(); p["review_gate"] = {"reviewer_required": True}; cases.append(p)
    p = valid_proposal(); p["baseline_sha256"] = "bad"; cases.append(p)
    p = valid_proposal(); p["risk"] = "extreme"; cases.append(p)
    p = valid_proposal(); p["edit_type"] = "rewrite"; cases.append(p)
    p = valid_proposal(); p["target_section"] = ""; cases.append(p)
    p = valid_proposal(); p["evidence"] = []; cases.append(p)
    p = valid_proposal(); p["privacy_sanitized"] = False; cases.append(p)
    p = valid_proposal(); p["review_gate"]["automatic_accept"] = True; cases.append(p)
    p = valid_proposal(); p["rationale"] = "leak /Users/example/private.md"; cases.append(p)
    for proposal in cases:
        assert not mod.validate_proposal(proposal).ok


def test_generation_creates_deterministic_verification_and_runtime_proposals() -> None:
    root = fixture_root()
    first = root / ".omx/reports/skillopt/out1"
    second = root / ".omx/reports/skillopt/out2"
    proc1 = run_generate(root, first)
    proc2 = run_generate(root, second)
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    first_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(first)]
    second_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(second)]
    assert first_payloads == second_payloads
    assert len(first_payloads) == 2  # duplicate missing_verification gap deduped
    by_gap = {tuple(item["source_gap_codes"]): item for item in first_payloads}
    verification = by_gap[("missing_verification",)]
    assert verification["edit_type"] == "add"
    assert "Verification" in verification["target_section"]
    assert verification["baseline_sha256"]
    assert verification["requires_human_review"] is True
    assert "eval_summary" in verification
    serialized = json.dumps(verification, ensure_ascii=False)
    assert "improved" not in serialized
    runtime = by_gap[("runtime_unmapped",)]
    assert "Runtime linkage" in runtime["target_section"]


def test_documented_generation_without_as_of_is_deterministic_from_inputs() -> None:
    root = fixture_root()
    first = root / ".omx/reports/skillopt/default1"
    second = root / ".omx/reports/skillopt/default2"
    proc1 = run_generate_without_as_of(root, first)
    proc2 = run_generate_without_as_of(root, second)
    assert proc1.returncode == 0, proc1.stdout + proc1.stderr
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr
    first_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(first)]
    second_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(second)]
    assert first_payloads == second_payloads



def test_generation_rejects_stale_baseline_hash_without_mutating() -> None:
    root = fixture_root()
    audit_path = root / ".omx/reports/skillopt/audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["skills"][0]["content_sha256"] = "b" * 64
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    proc = run_generate(root, root / ".omx/reports/skillopt/out")
    assert proc.returncode != 0
    assert "baseline hash does not match" in proc.stderr
    assert "/Users/" not in proc.stderr


def test_generation_invalid_absolute_skill_path_error_is_redacted() -> None:
    root = fixture_root()
    audit_path = root / ".omx/reports/skillopt/audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["skills"][0]["name"] = "private-skill"
    audit["skills"][0]["path"] = "/Users/example/private/SKILL.md"
    audit_path.write_text(json.dumps(audit), encoding="utf-8")
    proc = run_generate(root, root / ".omx/reports/skillopt/out")
    assert proc.returncode != 0
    assert "absolute skill paths" in proc.stderr
    assert "/Users/example" not in proc.stderr

def test_reject_appends_jsonl_and_suppresses_duplicate_by_default() -> None:
    root = fixture_root()
    out_dir = root / ".omx/reports/skillopt/out"
    proc = run_generate(root, out_dir)
    assert proc.returncode == 0, proc.stderr
    proposal = proposal_files(out_dir)[0]
    buffer = root / ".omx/reports/skillopt/rejected-edits.jsonl"
    original = '{"schema_version":"skillopt-rejected-edit.v1","fingerprint":"old"}\n'
    write(buffer, original)
    reject = subprocess.run(
        [sys.executable, str(SCRIPT), "reject", str(proposal), "--reason", "weak evidence", "--reviewer", "critic", "--buffer", str(buffer), "--as-of", "2026-06-27T00:00:00+09:00"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert reject.returncode == 0, reject.stdout + reject.stderr
    lines = buffer.read_text(encoding="utf-8").splitlines()
    assert lines[0] == original.strip()
    assert len(lines) == 2
    record = json.loads(lines[1])
    assert record["schema_version"] == "skillopt-rejected-edit.v1"
    assert record["reviewer"] == "critic"
    assert record["rejected_at"] == "2026-06-27T00:00:00+09:00"

    regen = run_generate(root, root / ".omx/reports/skillopt/out2", "--rejected-buffer", str(buffer))
    assert regen.returncode == 0, regen.stderr
    suppressed_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(root / ".omx/reports/skillopt/out2")]
    assert all(item["fingerprint"] != record["fingerprint"] for item in suppressed_payloads)

    include = run_generate(root, root / ".omx/reports/skillopt/out3", "--rejected-buffer", str(buffer), "--include-rejected")
    assert include.returncode == 0, include.stderr
    included_payloads = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(root / ".omx/reports/skillopt/out3")]
    rejected_again = [item for item in included_payloads if item["fingerprint"] == record["fingerprint"]]
    assert rejected_again and rejected_again[0]["previously_rejected"] is True


def test_rejected_regeneration_cleans_stale_candidate_in_same_out_dir() -> None:
    root = fixture_root()
    out_dir = root / ".omx/reports/skillopt/reused"
    assert run_generate(root, out_dir).returncode == 0
    proposal = proposal_files(out_dir)[0]
    rejected_fp = json.loads(proposal.read_text(encoding="utf-8"))["fingerprint"]
    buffer = root / ".omx/reports/skillopt/rejected-edits.jsonl"
    reject = subprocess.run(
        [sys.executable, str(SCRIPT), "reject", str(proposal), "--reason", "weak evidence", "--buffer", str(buffer)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert reject.returncode == 0, reject.stdout + reject.stderr
    regen = run_generate(root, out_dir, "--rejected-buffer", str(buffer))
    assert regen.returncode == 0, regen.stdout + regen.stderr
    remaining = [json.loads(path.read_text(encoding="utf-8")) for path in proposal_files(out_dir)]
    assert all(item["fingerprint"] != rejected_fp for item in remaining)


def test_reject_reason_privacy_guard_blocks_append() -> None:
    root = fixture_root()
    out_dir = root / ".omx/reports/skillopt/out"
    assert run_generate(root, out_dir).returncode == 0
    proposal = proposal_files(out_dir)[0]
    buffer = root / ".omx/reports/skillopt/rejected.jsonl"
    reject = subprocess.run(
        [sys.executable, str(SCRIPT), "reject", str(proposal), "--reason", "leak sk-abcdefghijklmnopqrstuvwxyz", "--buffer", str(buffer)],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert reject.returncode != 0
    assert not buffer.exists()
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in reject.stderr


def test_protected_output_paths_are_rejected() -> None:
    root = fixture_root()
    protected_out = run_generate(root, root / ".codex/skills/proposal-out")
    assert protected_out.returncode != 0
    assert "protected skill/runtime surfaces" in protected_out.stderr
    safe_out = root / ".omx/reports/skillopt/out"
    assert run_generate(root, safe_out).returncode == 0
    proposal = proposal_files(safe_out)[0]
    protected_buffer = subprocess.run(
        [sys.executable, str(SCRIPT), "reject", str(proposal), "--reason", "weak", "--buffer", str(root / "runtime/rejected.jsonl")],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert protected_buffer.returncode != 0
    assert "protected skill/runtime surfaces" in protected_buffer.stderr


def test_validate_lineage_accepts_and_rejects_gate_variants(tmp_path: Path) -> None:
    mod = load_propose()
    valid = {
        "schema_version": "skillopt-accepted-lineage.v1",
        "proposal_id": "p1",
        "fingerprint": "c" * 64,
        "skill": "example",
        "skill_path": ".codex/skills/example/SKILL.md",
        "before_sha256": "a" * 64,
        "after_sha256": "b" * 64,
        "eval_before": {"schema_version": "skillopt-eval.v1", "summary": {"status": "PASS"}},
        "eval_after": {"schema_version": "skillopt-eval.v1", "summary": {"status": "PASS"}},
        "reviewer_verdict": "APPROVE",
        "critic_verdict": "APPROVE",
        "commit_id": None,
        "accepted_at": "2026-06-27T00:00:00+09:00",
    }
    assert mod.validate_lineage_record(valid).ok
    bad = dict(valid, reviewer_verdict="NEEDS_CHANGES")
    assert not mod.validate_lineage_record(bad).ok
    bad = dict(valid, after_sha256="a" * 64)
    assert not mod.validate_lineage_record(bad).ok
    meta = dict(valid, after_sha256="a" * 64, metadata_only=True)
    assert mod.validate_lineage_record(meta).ok

    lineage = tmp_path / "lineage.jsonl"
    lineage.write_text(json.dumps(valid) + "\nnot-json\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "validate-lineage", str(lineage)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert proc.returncode != 0
    assert "line 2" in proc.stdout


def test_generation_and_reject_do_not_mutate_skill_or_runtime_files() -> None:
    root = fixture_root()
    runtime_agents = root / "runtime/agents.yaml"
    runtime_jobs = root / "runtime/jobs.yaml"
    write(runtime_agents, "agents: []\n")
    write(runtime_jobs, "jobs: []\n")
    tracked = [root / ".codex/skills/example/SKILL.md", runtime_agents, runtime_jobs]
    before = {path: path.read_text(encoding="utf-8") for path in tracked}
    out_dir = root / ".omx/reports/skillopt/out"
    assert run_generate(root, out_dir).returncode == 0
    proposal = proposal_files(out_dir)[0]
    buffer = root / ".omx/reports/skillopt/rejected.jsonl"
    subprocess.run([sys.executable, str(SCRIPT), "reject", str(proposal), "--reason", "not specific", "--buffer", str(buffer)], cwd=root, check=False)
    after = {path: path.read_text(encoding="utf-8") for path in tracked}
    assert before == after
    skill_text = (root / ".codex/skills/example/SKILL.md").read_text(encoding="utf-8")
    proposal_payload = json.loads(proposal.read_text(encoding="utf-8"))
    assert proposal_payload["patch"] not in skill_text


def test_fingerprint_stable_and_sensitive_to_core_fields() -> None:
    mod = load_propose()
    proposal = valid_proposal()
    base = mod.proposal_fingerprint(proposal)
    proposal_with_time = dict(proposal, generated_at="tomorrow")
    assert mod.proposal_fingerprint(proposal_with_time) == base
    for key, value in (
        ("baseline_sha256", "b" * 64),
        ("target_section", "Other"),
        ("patch", "## Other\n"),
        ("source_gap_codes", ["weak_output_contract"]),
    ):
        changed = dict(proposal)
        changed[key] = value
        assert mod.proposal_fingerprint(changed) != base


def test_validate_proposal_cli_and_no_secret_echo(tmp_path: Path) -> None:
    valid_path = tmp_path / "valid.json"
    invalid_path = tmp_path / "invalid.json"
    valid_path.write_text(json.dumps(valid_proposal()), encoding="utf-8")
    invalid = valid_proposal(); invalid["rationale"] = "leak /Users/example/private.md"
    invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
    ok = subprocess.run([sys.executable, str(SCRIPT), "validate-proposal", str(valid_path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    bad = subprocess.run([sys.executable, str(SCRIPT), "validate-proposal", str(invalid_path)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert ok.returncode == 0
    assert bad.returncode != 0
    assert "/Users/example" not in bad.stdout + bad.stderr


def test_real_repo_smoke_generates_at_least_one_candidate() -> None:
    audit = ROOT / ".omx/reports/skillopt/skillopt-audit-latest.json"
    eval_report = ROOT / ".omx/reports/skillopt/skillopt-eval-latest.json"
    if not audit.exists() or not eval_report.exists():
        return
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(ROOT), "--audit", str(audit), "--eval", str(eval_report), "--out-dir", str(Path(tmp) / "candidates"), "--as-of", "2026-06-27T00:00:00+09:00"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        summary = json.loads(proc.stdout)
        assert summary["count"] >= 1
        serialized = "\n".join(path.read_text(encoding="utf-8") for path in Path(tmp).glob("candidates/*/*.json"))
        assert "/Users/" not in serialized
        assert "discord.com/api/webhooks" not in serialized
