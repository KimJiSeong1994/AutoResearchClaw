"""Drift detection between the academic-technical-filter skill and its two implementations.

`.codex/skills/academic-technical-filter/SKILL.md` is loaded by no runtime process.
Its verdict rules exist twice in code:

- `skillopt_eval.classify_academic_case` gates SkillOpt's reward signal for the skill;
- `newsletter_ingest.academic_technical_eligibility` is what the Miner and
  paper-recommender intake paths actually call.

Nothing detected divergence between the two, or between either one and the skill
document. This module locks the agreements *and* the known differences.

Every case below triggers exactly one signal in its family. That matters: an
earlier version of this module used multi-signal cases, and deliberate mutations
of both implementations went undetected because a second overlapping signal kept
the verdict unchanged. `test_corpus_cases_are_single_signal` guards that property.

Known, deliberate difference in technical-report strictness
-----------------------------------------------------------
SKILL.md says to accept when "at least one strong public signal is present".
The evaluator takes that literally: one technical keyword plus a URL is eligible.
The mirror reads "strong" as a known technical/academic host, a GitHub repo path,
or two independent keywords (`newsletter_ingest.py:860-870`). So a technical
article on an unfamiliar host with a single keyword is eligible to the evaluator
and rejected by the mirror. Both thresholds are pinned below, so a change to
either side fails this module instead of silently shifting intake behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/skillopt/academic-technical-filter/heldout"
SKILL_MD = ROOT / ".codex/skills/academic-technical-filter/SKILL.md"

sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/paper-recommender"))

import newsletter_ingest  # noqa: E402
import skillopt_eval  # noqa: E402

# Academic sources SKILL.md names. Each must be recognized from the URL alone —
# the spaced display names ("acl anthology") never match a bare hostname, which
# is how aclanthology.org, semanticscholar.org, and paperswithcode.com were
# silently scored needs_review by the evaluator.
ACADEMIC_HOST_URLS = (
    "https://arxiv.org/abs/2401.00001",
    "https://doi.org/10.1000/xyz",
    "https://openreview.net/forum?id=abc",
    "https://www.semanticscholar.org/paper/abc",
    "https://aclanthology.org/2024.acl-1.1/",
    "https://proceedings.mlr.press/v202/x.html",
    "https://neurips.cc/virtual/2024/poster/1",
    "https://paperswithcode.com/sota/x",
)

# (case_id, summary) — one reject signal each, on a neutral host and title.
REJECT_SINGLES = (
    ("hiring", "We are hiring."),
    ("job", "New job alert."),
    ("career", "Career ladder update."),
    ("recruit", "Recruit event."),
    ("profile-views", "Profile views summary."),
    ("impressions", "Impressions this week."),
    ("analytics", "Analytics digest."),
    ("login", "Login notice."),
    ("notion", "Notion page update."),
    ("funding", "Funding round."),
    ("pricing", "Pricing change."),
    ("unsubscribe", "Unsubscribe here."),
)

# Two independent technical keywords: eligible on both sides.
TECHNICAL_PAIRS = (
    ("gpu-inference", "GPU inference optimization."),
    ("rag-retrieval", "RAG retrieval pipeline."),
    ("llm-benchmark", "LLM benchmark results."),
    ("cuda-serving", "CUDA serving stack."),
    ("multimodal-vision", "Multimodal vision study."),
)

# One technical keyword on an unfamiliar host: eligible to the evaluator,
# rejected by the mirror. This is the documented threshold difference.
TECHNICAL_SINGLES = (
    ("gpu", "GPU tuning notes."),
    ("cuda", "CUDA kernels explained."),
    ("benchmark", "Benchmark results."),
    ("rag", "RAG notes."),
    ("llm", "LLM notes."),
    ("serving", "Serving stack notes."),
    ("multimodal", "Multimodal notes."),
)

NEUTRAL_HOST = "https://blog.example.com/post"
NEUTRAL_TITLE = "Untitled"

PRIVATE_MARKER_CASES = {
    "needs-review-private-bait",
    "private-only-technical-signal",
    "private-title-technical-signal",
    "private-url-technical-signal",
}

DECLARED_VERDICTS = {"eligible", "reject", "needs_review"}
DECLARED_BUCKETS = {"academic_search", "technical_report", "out_of_scope"}


def both_verdicts(title: str, url: str, summary: str) -> tuple[tuple[str, str], tuple[str, str]]:
    """Run one item through both implementations, returning (verdict, bucket) pairs.

    Only public-surface keys are populated on the mirror item: it reads url,
    article_title/title, and article_description/public_excerpt/summary/snippet.
    """
    evaluated = skillopt_eval.classify_academic_case({"title": title, "url": url, "summary": summary})
    mirrored = newsletter_ingest.academic_technical_eligibility(
        {
            "url": url,
            "title": title,
            "article_title": title,
            "summary": summary,
            "article_description": summary,
        }
    )
    return (evaluated["verdict"], evaluated["bucket"]), (mirrored.verdict, mirrored.bucket)


def signal_count(title: str, url: str, summary: str) -> int:
    text = f"{title} {summary} {url}".lower()
    families = (skillopt_eval.ACADEMIC_SIGNALS, skillopt_eval.TECHNICAL_SIGNALS, skillopt_eval.REJECT_SIGNALS)
    return sum(1 for family in families for signal in family if signal in text)


def load_fixture_cases() -> list[dict[str, Any]]:
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(FIXTURES.glob("*.json"))]
    assert cases, "no held-out fixtures found"
    return cases


def test_corpus_cases_are_single_signal() -> None:
    """Guard the guard: overlapping signals make a drift corpus blind to mutations."""
    for case_id, summary in (*REJECT_SINGLES, *TECHNICAL_SINGLES):
        count = signal_count(NEUTRAL_TITLE, NEUTRAL_HOST, summary)
        assert count == 1, f"{case_id}: expected exactly 1 signal, found {count}"
    for case_id, summary in TECHNICAL_PAIRS:
        count = signal_count(NEUTRAL_TITLE, NEUTRAL_HOST, summary)
        assert count == 2, f"{case_id}: expected exactly 2 signals, found {count}"
    assert len(ACADEMIC_HOST_URLS) >= 8
    assert len(REJECT_SINGLES) >= 10


def test_declared_academic_sources_are_eligible_from_url_alone() -> None:
    """Every academic source SKILL.md names must survive a terse title and summary."""
    for url in ACADEMIC_HOST_URLS:
        evaluated, mirrored = both_verdicts(NEUTRAL_TITLE, url, "Paper.")
        assert evaluated == ("eligible", "academic_search"), f"evaluator missed academic source {url}: {evaluated}"
        assert mirrored == ("eligible", "academic_search"), f"mirror missed academic source {url}: {mirrored}"


def test_reject_rules_agree_on_both_sides() -> None:
    for case_id, summary in REJECT_SINGLES:
        evaluated, mirrored = both_verdicts(NEUTRAL_TITLE, NEUTRAL_HOST, summary)
        assert evaluated == ("reject", "out_of_scope"), f"{case_id}: evaluator returned {evaluated}"
        assert mirrored == ("reject", "out_of_scope"), f"{case_id}: mirror returned {mirrored}"


def test_two_technical_signals_are_eligible_on_both_sides() -> None:
    for case_id, summary in TECHNICAL_PAIRS:
        evaluated, mirrored = both_verdicts(NEUTRAL_TITLE, NEUTRAL_HOST, summary)
        assert evaluated == ("eligible", "technical_report"), f"{case_id}: evaluator returned {evaluated}"
        assert mirrored == ("eligible", "technical_report"), f"{case_id}: mirror returned {mirrored}"


def test_single_technical_signal_threshold_difference_is_unchanged() -> None:
    """Pin both thresholds: evaluator accepts one keyword, the mirror requires two.

    If either side moves, this fails and the change must be reviewed rather than
    silently altering what the Miner intake collects.
    """
    for case_id, summary in TECHNICAL_SINGLES:
        evaluated, mirrored = both_verdicts(NEUTRAL_TITLE, NEUTRAL_HOST, summary)
        assert evaluated == ("eligible", "technical_report"), f"{case_id}: evaluator moved to {evaluated}"
        assert mirrored == ("reject", "out_of_scope"), f"{case_id}: mirror moved to {mirrored}"


def test_private_marker_cases_never_yield_eligible_from_evaluator() -> None:
    for case in load_fixture_cases():
        if case["id"] not in PRIVATE_MARKER_CASES:
            continue
        result = skillopt_eval.classify_academic_case(case["input"])
        assert result["verdict"] != "eligible", f"{case['id']}: private evidence promoted to eligible"


def test_output_vocabulary_matches_skill_contract() -> None:
    skill_text = SKILL_MD.read_text(encoding="utf-8")
    for verdict in DECLARED_VERDICTS:
        assert f"`{verdict}`" in skill_text, f"SKILL.md no longer declares verdict {verdict}"
    for bucket in DECLARED_BUCKETS:
        assert bucket in skill_text, f"SKILL.md no longer declares bucket {bucket}"

    probes = [(NEUTRAL_TITLE, url, "Paper.") for url in ACADEMIC_HOST_URLS]
    probes += [(NEUTRAL_TITLE, NEUTRAL_HOST, summary) for _, summary in (*REJECT_SINGLES, *TECHNICAL_PAIRS, *TECHNICAL_SINGLES)]
    for title, url, summary in probes:
        for verdict, bucket in both_verdicts(title, url, summary):
            assert verdict in DECLARED_VERDICTS, f"undeclared verdict {verdict}"
            assert bucket in DECLARED_BUCKETS, f"undeclared bucket {bucket}"


# Reject-signal content that merely links to an academic source. Widening
# ACADEMIC_SIGNALS with hostname forms made these flip from reject to eligible,
# because the evaluator checked academic signals before reject signals.
REJECT_WITH_ACADEMIC_LINK = (
    ("profile-notice-links-pwc", "You have 5 profile views", "https://social.example.com/5", "Your paperswithcode.com link got impressions."),
    ("job-ad-links-acl", "Research Engineer hiring", "https://jobs.example.com/6", "See https://aclanthology.org/2024.acl-1.1/ ; apply for this career move."),
    ("job-ad-names-neurips", "NeurIPS Research Scientist opening", "https://jobs.example.com/1", "We are hiring; publications at neurips.cc a plus."),
    ("ad-links-openreview", "Sponsored: our platform", "https://ads.example.com/3", "Integrates with openreview.net submissions. Pricing available."),
)


def test_reject_signal_beats_a_bare_academic_link() -> None:
    """SKILL.md rejects jobs and notices "even if a newsletter supplied it"."""
    for case_id, title, url, summary in REJECT_WITH_ACADEMIC_LINK:
        evaluated, mirrored = both_verdicts(title, url, summary)
        assert evaluated == ("reject", "out_of_scope"), f"{case_id}: evaluator returned {evaluated}"
        assert mirrored == ("reject", "out_of_scope"), f"{case_id}: mirror returned {mirrored}"


def test_reject_precedence_does_not_swallow_genuine_academic_sources() -> None:
    """The precedence rule must not make real papers unreachable."""
    for url in ACADEMIC_HOST_URLS:
        evaluated, mirrored = both_verdicts("Coref Resolution", url, "Paper.")
        assert evaluated == ("eligible", "academic_search"), f"{url}: evaluator returned {evaluated}"
        assert mirrored == ("eligible", "academic_search"), f"{url}: mirror returned {mirrored}"
