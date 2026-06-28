from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "skillopt_eval.py"


def load_eval() -> Any:
    spec = importlib.util.spec_from_file_location("skillopt_eval", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_skillopt_eval_harness_passes_heldout_fixtures() -> None:
    mod = load_eval()
    report = mod.make_report(mod.parse_args(["--root", str(ROOT), "--as-of", "2026-06-27T00:00:00+09:00"]))
    assert report["schema_version"] == "skillopt-eval.v1"
    assert report["summary"] == {"total": 8, "passed": 8, "failed": 0, "status": "PASS"}
    skills = {result["skill"] for result in report["results"]}
    assert skills == {"academic-technical-filter", "blog-research-post", "jiphyeonjeon-reporter-article-post"}
    assert report["acceptance_policy"]["automatic_accept"] is False
    assert report["acceptance_policy"]["requires_reviewer_gate"] is True


def test_academic_filter_private_bait_does_not_leak_to_evidence() -> None:
    mod = load_eval()
    report = mod.make_report(mod.parse_args(["--root", str(ROOT), "--as-of", "2026-06-27T00:00:00+09:00"]))
    bait = next(result for result in report["results"] if result["case_id"] == "needs-review-private-bait")
    serialized_actual = json.dumps(bait["details"]["actual"], ensure_ascii=False)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in serialized_actual
    assert bait["details"]["actual"]["verdict"] == "needs_review"



def test_academic_filter_private_only_technical_signal_does_not_drive_verdict() -> None:
    mod = load_eval()
    report = mod.make_report(mod.parse_args(["--root", str(ROOT), "--as-of", "2026-06-27T00:00:00+09:00"]))
    result = next(result for result in report["results"] if result["case_id"] == "private-only-technical-signal")
    assert result["passed"] is True
    assert result["details"]["actual"]["verdict"] == "needs_review"
    assert result["details"]["actual"]["reason"] == "weak_public_evidence"


def test_academic_filter_private_title_or_url_technical_signal_does_not_drive_verdict() -> None:
    mod = load_eval()
    report = mod.make_report(mod.parse_args(["--root", str(ROOT), "--as-of", "2026-06-27T00:00:00+09:00"]))
    for case_id in ("private-title-technical-signal", "private-url-technical-signal"):
        result = next(result for result in report["results"] if result["case_id"] == case_id)
        assert result["passed"] is True
        assert result["details"]["actual"]["verdict"] == "needs_review"
        assert result["details"]["actual"]["reason"] == "weak_public_evidence"

def test_blog_validator_rejects_named_personal_style_imitation() -> None:
    mod = load_eval()
    text = """# 제목
대표 이미지 설명: 추상 이미지
> 3줄 요약
## 왜 지금 이 이슈인가
## 핵심 주장
## 논증 구조
반론
판단
## 카드뉴스 재사용안
## 디스코드 브리핑 재사용안
## 출처
https://example.com
양승훈 교수 문체로 작성한다.
"""
    errors = mod.validate_blog_markdown(text)
    assert any("personal style imitation" in error for error in errors)


def test_sanitize_process_output_redacts_full_users_path_with_s_in_username() -> None:
    mod = load_eval()
    root = ROOT
    text = "draft=/Users/jiseong/private-note.md appendix=/Users/jiseong/other.md\n"
    sanitized = mod.sanitize_process_output(text, root)
    assert "/Users/" not in sanitized
    assert "jiseong" not in sanitized
    assert "private-note.md" not in sanitized
    assert sanitized.count("[redacted-local-path]") == 2


def test_cli_writes_deterministic_report() -> None:
    out = ROOT / ".omx/reports/skillopt/skillopt-eval-latest.json"
    cmd = [sys.executable, str(SCRIPT), "--root", str(ROOT), "--as-of", "2026-06-27T00:00:00+09:00", "--out", str(out)]
    first = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    second = subprocess.run(cmd, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert first.stdout == second.stdout
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["summary"]["status"] == "PASS"
    serialized = json.dumps(saved, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "discord.com/api/webhooks" not in serialized
