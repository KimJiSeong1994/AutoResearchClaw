from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".codex" / "skills" / "jiphyeonjeon-reporter-article-post" / "SKILL.md"
VALIDATOR = ROOT / ".codex" / "skills" / "jiphyeonjeon-reporter-article-post" / "scripts" / "validate_article_post.py"
DRAFT = ROOT / "workspace" / "blog-drafts" / "agentic-enterprise-google-cloud-next-korean-tech-intro-20260524.md"
APPENDIX = ROOT / "workspace" / "blog-drafts" / "agentic-enterprise-blog-evidence-appendix-20260524.md"
REGENERATED_DRAFT = ROOT / "workspace" / "blog-drafts" / "agentic-enterprise-google-cloud-next-korean-tech-intro-regenerated-20260524.md"
REGENERATED_APPENDIX = ROOT / "workspace" / "blog-drafts" / "agentic-enterprise-blog-regenerated-evidence-appendix-20260524.md"


def test_reporter_article_skill_declares_public_internal_split() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "Public vs internal split" in text
    assert "Two-layer claim model" in text
    assert "validate_article_post.py" in text
    assert "실제 게시 또는 live API write" in text


def test_reporter_article_validator_accepts_generated_draft() -> None:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--draft", str(DRAFT), "--appendix", str(APPENDIX)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "validation: PASS" in proc.stdout


def test_public_draft_keeps_internal_evidence_out_of_article_body() -> None:
    text = DRAFT.read_text(encoding="utf-8")
    assert ".omx" not in text
    assert "workspace/" not in text
    assert "published: false" in text
    appendix = APPENDIX.read_text(encoding="utf-8")
    assert ".omx/reports/daily-trends-2026-05-04.md:5-8" in appendix


def test_reporter_article_validator_accepts_regenerated_draft() -> None:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR), "--draft", str(REGENERATED_DRAFT), "--appendix", str(REGENERATED_APPENDIX)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "validation: PASS" in proc.stdout
