#!/usr/bin/env python3
"""Deterministic SkillOpt evaluation harness for AutoResearchClaw skills.

Phase 2 intentionally evaluates skill behavior/contracts without mutating skill
files. It uses small gold/held-out fixtures so future bounded edits can be
accepted or rejected with evidence instead of subjective prompt review alone.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from skillopt_common import read_json
except ModuleNotFoundError:  # pragma: no cover - direct path fallback in tests
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from skillopt_common import read_json

SCHEMA_VERSION = "skillopt-eval.v1"

SECRET_RE = re.compile(
    r"(?i)(/Users/|Mobile Documents|discord(?:app)?\.com/api/webhooks/|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-|api[_ -]?key|bot[_ -]?token|relay[_ -]?read[_ -]?token|private email body|mailbox-only)"
)
URL_RE = re.compile(r"https?://")
PRIVATE_EVIDENCE_MARKER_RE = re.compile(
    r"(?is)(private email body|mailbox-only|private mailbox|internal email body|raw email body).*"
)

ACADEMIC_SIGNALS = (
    "arxiv", "doi", "openreview", "semantic scholar", "acl anthology", "pmlr",
    "neurips", "icml", "papers with code",
    # Hostname forms: the spaced display names above never match a bare URL such
    # as aclanthology.org, so SKILL.md-eligible sources were scored needs_review
    # whenever the title/summary did not spell the name out.
    # Only the four whose display name is not already a substring of the host:
    # neurips.cc/icml.cc/openreview.net already match via "neurips"/"icml"/"openreview".
    "semanticscholar.org", "aclanthology.org", "paperswithcode.com", "proceedings.mlr.press",
)
TECHNICAL_SIGNALS = (
    "rag", "retrieval", "knowledge graph", "llm", "agent", "model", "machine learning",
    "benchmark", "evaluation", "inference", "serving", "gpu", "cuda", "multimodal",
    "vision", "security", "privacy", "api", "framework", "library", "architecture",
    "engineering", "technical report", "research blog",
)
REJECT_SIGNALS = (
    "job", "hiring", "career", "recruit", "profile views", "impressions", "analytics",
    "unsubscribe", "terms", "privacy settings", "login", "notion page update",
    "funding", "pricing", "partnership", "market update",
)

BLOG_REQUIRED_MARKERS = (
    "대표 이미지", "3줄 요약", "왜 지금 이 이슈인가", "핵심 주장", "논증 구조",
    "반론", "판단", "카드뉴스 재사용안", "디스코드 브리핑 재사용안", "출처",
)
BLOG_FORBIDDEN_STYLE = ("양승훈처럼", "양승훈 교수 문체", "특정 개인 문체", "문체 모사")


@dataclass(frozen=True)
class CaseResult:
    skill: str
    case_id: str
    passed: bool
    errors: list[str]
    details: dict[str, Any]


# read_json imported from skillopt_common


def has_secret(value: str) -> bool:
    return bool(SECRET_RE.search(value))


def classify_academic_case(item: dict[str, Any]) -> dict[str, Any]:
    title = public_only_text(str(item.get("title", "")))
    summary = public_only_text(str(item.get("summary", "")))
    url = public_only_text(str(item.get("url", "")))
    text = f"{title} {summary} {url}".lower()
    evidence: list[str] = []
    # Reject wins over a bare academic mention. SKILL.md: "Reject even if a
    # newsletter supplied it" — a job ad or profile notice that merely links to
    # arxiv/aclanthology is still a job ad. The mirror already gives reject
    # hints this precedence (newsletter_ingest.py, out_of_scope before signal
    # scoring), so checking academic first also drifted the two apart.
    if any(signal in text for signal in REJECT_SIGNALS):
        evidence.append("out-of-scope public signal")
        return {"verdict": "reject", "bucket": "out_of_scope", "reason": "nontechnical_or_admin_signal", "evidence": evidence}
    if any(signal in text for signal in ACADEMIC_SIGNALS):
        evidence.append("academic-search public signal")
        return {"verdict": "eligible", "bucket": "academic_search", "reason": "academic_public_signal", "evidence": evidence}
    if URL_RE.search(url) and any(signal in text for signal in TECHNICAL_SIGNALS):
        evidence.append("technical-report public signal")
        return {"verdict": "eligible", "bucket": "technical_report", "reason": "technical_public_signal", "evidence": evidence}
    return {"verdict": "needs_review", "bucket": "out_of_scope", "reason": "weak_public_evidence", "evidence": ["weak public evidence"]}


def public_only_text(value: str) -> str:
    """Remove private/mailbox-only bait before classification decisions."""
    return PRIVATE_EVIDENCE_MARKER_RE.sub("", value)


def eval_academic(root: Path, fixture_dir: Path) -> list[CaseResult]:
    results: list[CaseResult] = []
    for path in sorted(fixture_dir.glob("*.json")):
        case = read_json(path)
        actual = classify_academic_case(case["input"])
        expected = case["expected"]
        errors: list[str] = []
        for key in ("verdict", "bucket"):
            if actual.get(key) != expected.get(key):
                errors.append(f"{key}: expected {expected.get(key)!r}, got {actual.get(key)!r}")
        serialized = json.dumps(actual, ensure_ascii=False)
        if has_secret(serialized):
            errors.append("classification output contains private/secret-like evidence")
        if expected.get("forbid_private_evidence") and has_secret(json.dumps(case["input"], ensure_ascii=False)):
            # Input may contain private bait, but actual evidence must stay clean.
            if has_secret(serialized):
                errors.append("private bait leaked into evidence")
        results.append(CaseResult("academic-technical-filter", case.get("id", path.stem), not errors, errors, {"actual": actual}))
    return results


def validate_blog_markdown(text: str) -> list[str]:
    errors = [f"missing marker: {marker}" for marker in BLOG_REQUIRED_MARKERS if marker not in text]
    if not re.search(r"!\[[^\]]*대표 이미지|대표 이미지[^\n]*(프롬프트|설명)", text):
        errors.append("missing hero image description or prompt")
    if len(re.findall(r"https?://", text)) == 0:
        errors.append("missing source URL")
    if any(marker in text for marker in BLOG_FORBIDDEN_STYLE):
        errors.append("contains forbidden named personal style imitation")
    if has_secret(text) or ".omx" in text or "workspace/" in text:
        errors.append("contains private path/secret-like leakage")
    return errors


def eval_blog(root: Path, fixture_dir: Path) -> list[CaseResult]:
    results: list[CaseResult] = []
    for path in sorted(fixture_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        errors = validate_blog_markdown(text)
        results.append(CaseResult("blog-research-post", path.stem, not errors, errors, {"path": rel(path, root)}))
    return results


def eval_reporter(root: Path, fixture_dir: Path) -> list[CaseResult]:
    validator = root / ".codex/skills/jiphyeonjeon-reporter-article-post/scripts/validate_article_post.py"
    results: list[CaseResult] = []
    for path in sorted(fixture_dir.glob("*.json")):
        case = read_json(path)
        draft = root / case["draft"]
        appendix = root / case["appendix"] if case.get("appendix") else None
        cmd = [sys.executable, str(validator), "--draft", str(draft)]
        if appendix is not None:
            cmd.extend(["--appendix", str(appendix)])
        proc = subprocess.run(cmd, cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        errors: list[str] = []
        expected_pass = bool(case.get("expected_pass", True))
        if expected_pass and proc.returncode != 0:
            errors.append(proc.stdout + proc.stderr)
        if not expected_pass and proc.returncode == 0:
            errors.append("validator unexpectedly passed")
        combined = sanitize_process_output(proc.stdout + proc.stderr, root)
        if has_secret(combined):
            errors.append("validator output contains private/secret-like leakage")
        results.append(
            CaseResult(
                "jiphyeonjeon-reporter-article-post",
                case.get("id", path.stem),
                not errors,
                errors,
                {"returncode": proc.returncode, "stdout": sanitize_process_output(proc.stdout, root).strip()},
            )
        )
    return results


def sanitize_process_output(value: str, root: Path) -> str:
    safe = value.replace(str(root.resolve()), ".")
    return re.sub(r"/Users/[^\s`|]+", "[redacted-local-path]", safe)


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def make_report(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).resolve()
    fixture_root = (root / args.fixtures).resolve()
    results: list[CaseResult] = []
    results.extend(eval_academic(root, fixture_root / "academic-technical-filter/heldout"))
    results.extend(eval_blog(root, fixture_root / "blog-research-post/heldout"))
    results.extend(eval_reporter(root, fixture_root / "jiphyeonjeon-reporter-article-post/heldout"))
    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": args.as_of or datetime.now(timezone.utc).isoformat(),
        "fixtures": rel(fixture_root, root),
        "summary": {"total": len(results), "passed": passed, "failed": failed, "status": "PASS" if failed == 0 else "FAIL"},
        "acceptance_policy": {
            "phase": "2-evaluation-harness",
            "automatic_accept": False,
            "requires_reviewer_gate": True,
            "privacy_checks_must_pass": True,
        },
        "results": [asdict(result) for result in results],
    }


def write_report(report: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SkillOpt deterministic evaluation harness")
    parser.add_argument("--root", default=".")
    parser.add_argument("--fixtures", default="tests/fixtures/skillopt")
    parser.add_argument("--as-of", default="")
    parser.add_argument("--out", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    report = make_report(args)
    if args.out:
        write_report(report, Path(args.out))
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
