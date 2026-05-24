#!/usr/bin/env python3
"""Validate a Jiphyeonjeon reporter article draft and internal appendix."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_SECTIONS = [
    "대표 이미지",
    "3줄 요약",
    "먼저 밝히는 근거 범위",
    "왜 지금 이 이슈인가",
    "주요 용어",
    "핵심 주장",
    "논증 구조",
    "근거 표",
    "산업사회학적",
    "앞으로 볼 질문",
    "한계와 주의",
    "카드뉴스 재사용안",
    "디스코드 브리핑 재사용안",
    "출처",
]

INTERNAL_LEAK_RE = re.compile(r"(\.omx|workspace/|/Users/|prd-|test-spec|ralplan-handoff|\.md:\d+)")
FORBIDDEN_RE = re.compile(
    r"(?i)(discord(?:app)?\.com/api/webhooks/|api[_ -]?key|secret|private workspace|raw paid|production publish|live api write|자동 게시된다|보장한다)"
)
LOCAL_EVIDENCE_RE = re.compile(r"\.omx/reports/[^`|\s]+\.md:\d+")
URL_RE = re.compile(r"https?://")


def fail(msg: str) -> str:
    return f"FAIL: {msg}"


def validate(draft: Path, appendix: Path | None) -> list[str]:
    errors: list[str] = []
    if not draft.exists():
        return [fail(f"draft not found: {draft}")]
    text = draft.read_text(encoding="utf-8")

    for section in REQUIRED_SECTIONS:
        if section not in text:
            errors.append(fail(f"missing required section marker: {section}"))

    if "published: false" not in text:
        errors.append(fail("frontmatter must include published: false"))
    if INTERNAL_LEAK_RE.search(text):
        errors.append(fail("public draft contains internal path/line leakage"))
    if FORBIDDEN_RE.search(text):
        errors.append(fail("public draft contains forbidden secret/production-publish wording"))
    if "YouTube" in text and not re.search(r"(제외|저신뢰|핵심 근거에서 제외|caveat)", text):
        errors.append(fail("YouTube/low-confidence source mention lacks exclusion/caveat wording"))
    if text.count("|") < 20 or "신뢰/검증" not in text:
        errors.append(fail("evidence table with confidence/verification appears incomplete"))
    if not URL_RE.search(text):
        errors.append(fail("public draft has no public URLs"))

    if appendix is not None:
        if not appendix.exists():
            errors.append(fail(f"appendix not found: {appendix}"))
        else:
            appendix_text = appendix.read_text(encoding="utf-8")
            if not LOCAL_EVIDENCE_RE.search(appendix_text):
                errors.append(fail("appendix lacks local .omx report line references"))
            if "Internal Evidence Appendix" not in appendix_text:
                errors.append(fail("appendix missing Internal Evidence Appendix heading"))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draft", required=True, type=Path)
    parser.add_argument("--appendix", type=Path)
    args = parser.parse_args()
    errors = validate(args.draft, args.appendix)
    if errors:
        print("jiphyeonjeon-reporter-article-post validation: FAIL")
        for error in errors:
            print(error)
        return 1
    print("jiphyeonjeon-reporter-article-post validation: PASS")
    print(f"draft={args.draft}")
    if args.appendix:
        print(f"appendix={args.appendix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
