from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillopt_audit.py"


def load_skillopt() -> Any:
    spec = importlib.util.spec_from_file_location("skillopt_audit", SCRIPT)
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
    root = Path(tmp.name)
    # Keep tempdir alive by attaching it to the path object through module global.
    _TEMPS.append(tmp)
    write(
        root / ".codex/skills/example/SKILL.md",
        """---
name: example
description: Use when testing SkillOpt audit.
---
# Example Skill

## 입력
- report path

## 출력 구조
- JSON

## 워크플로우
1. inspect

## 공개/비공개 분리
Do not print token values.

## 검증 명령
pytest

## 근거 참조
Use references only when needed.
""",
    )
    write(
        root / "skills/example-runtime/SKILL.md",
        """---
name: example-runtime
description: Runtime skill.
---
# Runtime Skill

## Inputs
- queue

## Output format
- report

## Workflow
- run

## Safety
- fallback on missing input

## Checklist
- test
""",
    )
    write(
        root / "skills/example-runtime/README.md",
        """# Example Runtime README

## Usage
Runtime usage docs.

## Fallback
No production side effects.
""",
    )
    write(
        root / "runtime/agents.yaml",
        """version: 1
kind: runtime-agents
metadata:
  source_scope:
    - .codex/skills/*/SKILL.md
    - skills/*/SKILL.md
    - skills/*/README.md
agents:
  - id: example-agent
    name: Example
    kind: test-agent
    source_refs:
      - .codex/skills/example/SKILL.md
      - skills/example-runtime/SKILL.md
      - skills/example-runtime/README.md
    owns_jobs:
      - example-job
    responsibilities:
      - test
    boundaries:
      - no secrets
""",
    )
    write(
        root / "runtime/jobs.yaml",
        """version: 1
kind: runtime-jobs
metadata:
  source_scope:
    - .codex/skills/*/SKILL.md
    - skills/*/SKILL.md
    - skills/*/README.md
jobs:
  - id: example-job
    name: Example job
    type: local-check
    owner_agent: example-agent
    command_refs:
      - python3 scripts/skillopt_audit.py
    outputs:
      - report
    safety: read-only
""",
    )
    return root


_TEMPS: list[tempfile.TemporaryDirectory[str]] = []


def test_inventory_scans_codex_runtime_skill_and_runtime_readme_roots() -> None:
    mod = load_skillopt()
    root = fixture_root()
    report = mod.make_report(
        mod.parse_args(
            [
                "--root",
                str(root),
                "--as-of",
                "2026-06-27T00:00:00+09:00",
            ]
        )
    )
    assert report["schema_version"] == "skillopt-audit.v1"
    records = {(item["root_kind"], item["path"]): item for item in report["skills"]}
    assert ("codex_skill", ".codex/skills/example/SKILL.md") in records
    assert ("runtime_skill", "skills/example-runtime/SKILL.md") in records
    assert ("runtime_readme", "skills/example-runtime/README.md") in records
    assert records[("runtime_readme", "skills/example-runtime/README.md")]["name"] == "Example Runtime README"
    assert records[("codex_skill", ".codex/skills/example/SKILL.md")]["runtime_refs"]


def test_bilingual_heading_normalization_and_stable_gap_codes() -> None:
    mod = load_skillopt()
    root = fixture_root()
    report = mod.make_report(mod.parse_args(["--root", str(root), "--as-of", "2026-06-27T00:00:00+09:00"]))
    codex = next(item for item in report["skills"] if item["root_kind"] == "codex_skill")
    assert codex["dimensions"]["input_contract"]["present"] is True
    assert codex["dimensions"]["output_contract"]["present"] is True
    assert codex["dimensions"]["workflow"]["present"] is True
    assert codex["dimensions"]["safety_privacy"]["present"] is True
    assert codex["dimensions"]["verification"]["present"] is True
    for gap in codex["gaps"]:
        assert "gap_code" in gap
        assert gap["gap_code"] in report["gap_definitions"]


def test_paperwiki_evidence_is_sanitized_to_wiki_relative_paths() -> None:
    mod = load_skillopt()
    root = fixture_root()
    evidence = root / "paperwiki-result.md"
    vault = "/Users/example/Library/Mobile Documents/com~apple~CloudDocs/PaperWiki/PaperWiki"
    write(
        evidence,
        f"""# Graphsearch results

## 1. SkillOpt
- path: `{vault}/pages/10 Research Automation and Agent Infrastructure/paper-SkillOpt.md`
- tags: `method/skill-optimization`, `topic/agents`
- reasons: `content/token:skillopt`, `title`
""",
    )
    report = mod.make_report(
        mod.parse_args(
            [
                "--root",
                str(root),
                "--paperwiki-evidence",
                str(evidence),
                "--paperwiki-vault-root",
                vault,
                "--as-of",
                "2026-06-27T00:00:00+09:00",
            ]
        )
    )
    serialized = json.dumps(report["paperwiki_evidence"], ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "Mobile Documents" not in serialized
    assert report["paperwiki_evidence"][0]["path"] == "pages/10 Research Automation and Agent Infrastructure/paper-SkillOpt.md"
    assert report["paperwiki_evidence_gaps"] == []



def test_paperwiki_evidence_forbidden_absolute_path_is_flagged_without_echo() -> None:
    mod = load_skillopt()
    root = fixture_root()
    evidence = root / "bad-paperwiki-result.md"
    write(
        evidence,
        """# Graphsearch results

## 1. Bad /Users Title sk-abcdefghijklmnopqrstuvwxyz
- path: `/Users/example/private-note.md`
- tags: `topic/private`, `Mobile Documents`, `https://discord.com/api/webhooks/123/abcdefghiABCDEFGHI`
- reasons: `content/token:skillopt`, `/Users/example/body-leak`
""",
    )
    report = mod.make_report(
        mod.parse_args(
            [
                "--root",
                str(root),
                "--paperwiki-evidence",
                str(evidence),
                "--as-of",
                "2026-06-27T00:00:00+09:00",
            ]
        )
    )
    serialized = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "Mobile Documents" not in serialized
    assert "discord.com/api/webhooks" not in serialized
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in serialized
    assert "paperwiki_evidence_unsanitized" in {gap["gap_code"] for gap in report["paperwiki_evidence_gaps"]}


def test_paperwiki_forbidden_path_secret_like_basename_is_redacted() -> None:
    mod = load_skillopt()
    root = fixture_root()
    evidence = root / "secret-basename-paperwiki-result.md"
    write(
        evidence,
        """# Graphsearch results

## 1. Bad
- path: `/Users/example/sk-abcdefghijklmnopqrstuvwxyz.md`
- tags: `topic/private`
- reasons: `content/token:skillopt`
""",
    )
    report = mod.make_report(
        mod.parse_args(
            [
                "--root",
                str(root),
                "--paperwiki-evidence",
                str(evidence),
                "--as-of",
                "2026-06-27T00:00:00+09:00",
            ]
        )
    )
    serialized = json.dumps(report, ensure_ascii=False)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in serialized
    assert report["paperwiki_evidence"][0]["path"] == "redacted-paperwiki-path"
    assert "paperwiki_evidence_unsanitized" in {gap["gap_code"] for gap in report["paperwiki_evidence_gaps"]}


def test_paperwiki_custom_vault_root_metadata_is_flagged_without_echo() -> None:
    mod = load_skillopt()
    root = fixture_root()
    evidence = root / "custom-vault-paperwiki-result.md"
    vault = "/Volumes/ResearchVault/PaperWiki"
    write(
        evidence,
        f"""# Graphsearch results

## 1. SkillOpt from {vault}
- path: `{vault}/pages/skillopt.md`
- tags: `topic/agents`, `{vault}/tags/private`
- reasons: `source:{vault}/notes/body`
""",
    )
    report = mod.make_report(
        mod.parse_args(
            [
                "--root",
                str(root),
                "--paperwiki-evidence",
                str(evidence),
                "--paperwiki-vault-root",
                vault,
                "--as-of",
                "2026-06-27T00:00:00+09:00",
            ]
        )
    )
    serialized = json.dumps(report, ensure_ascii=False)
    assert vault not in serialized
    assert report["paperwiki_evidence"][0]["path"] == "pages/skillopt.md"
    assert "paperwiki_evidence_unsanitized" in {gap["gap_code"] for gap in report["paperwiki_evidence_gaps"]}


def test_deterministic_as_of_output_and_cli_write() -> None:
    root = fixture_root()
    out = root / ".omx/reports/skillopt/audit.json"
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--root",
        str(root),
        "--as-of",
        "2026-06-27T00:00:00+09:00",
        "--out",
        str(out),
        "--markdown",
    ]
    first = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    second = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["generated_at"] == "2026-06-27T00:00:00+09:00"
    assert out.with_suffix(".md").exists()


def test_real_repo_smoke_covers_reporter_skill_and_runtime_mapping() -> None:
    mod = load_skillopt()
    report = mod.make_report(mod.parse_args(["--root", str(ROOT), "--as-of", "2026-06-27T00:00:00+09:00"]))
    paths = {item["path"]: item for item in report["skills"]}
    reporter = paths[".codex/skills/jiphyeonjeon-reporter-article-post/SKILL.md"]
    assert reporter["root_kind"] == "codex_skill"
    assert reporter["runtime_refs"], "reporter skill should map to runtime publisher metadata/source_refs"
    assert "agent:jiphyeonjeon-blog-publisher" in reporter["runtime_refs"]
    assert "job:jiphyeonjeon-blog-publish" in reporter["runtime_refs"]
    assert any(item["root_kind"] == "runtime_readme" for item in report["skills"])
    assert all(item["schema_version"] == "skillopt-audit.v1" for item in [report])
