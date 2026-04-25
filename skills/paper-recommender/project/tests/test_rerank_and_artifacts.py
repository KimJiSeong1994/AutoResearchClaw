from __future__ import annotations

import json
import sys
import types
from pathlib import Path

# config.py imports yaml for load_settings; these tests instantiate dataclasses only.
sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda *_args, **_kwargs: {}))

from paper_recommender.config import (  # noqa: E402
    CandidateSettings,
    DecaySettings,
    FeedbackSettings,
    JiphySettings,
    OpenClawSettings,
    OutputSettings,
    ProfileSettings,
    RerankSettings,
    SeenSettings,
    Settings,
    SoulSettings,
)
from paper_recommender.candidates import paper_key  # noqa: E402
from paper_recommender.obsidian import render_note, write_artifacts  # noqa: E402
from paper_recommender.rerank import _candidate_line, _rank_to_score, score_stats  # noqa: E402


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        jiphyeonjeon=JiphySettings(base_url="http://jiphy", token_env="JIPHY_TOKEN", timeout_sec=10),
        openclaw=OpenClawSettings(
            base_url="http://openclaw",
            token_env="OPENCLAW_TOKEN",
            primary_model="primary",
            fallback_model="fallback",
            timeout_sec=10,
        ),
        profile=ProfileSettings(
            cache_ttl_days=7,
            seed_topics=["rag"],
            max_bookmarks_for_profile=5,
            narrative_enabled=True,
        ),
        candidates=CandidateSettings(
            per_keyword=5,
            related_per_bookmark=2,
            related_from_top_n_bookmarks=1,
            year_start=None,
            year_end=None,
            total_cap=20,
        ),
        rerank=RerankSettings(
            batch_size=3,
            top_k=3,
            min_score=3.0,
            temperature=0.2,
            mode="ab",
            scoring_mode="listwise",
            use_relevance_anchor=True,
        ),
        seen=SeenSettings(cooldown_days=30),
        soul=SoulSettings(
            enabled=True,
            update_cadence_days=1,
            max_bytes=3072,
            compact_at_bytes=2560,
            include_recent_picks_days=5,
        ),
        decay=DecaySettings(enabled=True, half_life_days=60),
        feedback=FeedbackSettings(
            enabled=True,
            lookback_days=7,
            max_file_kb=512,
            inbox_subdir="feedback_inbox",
        ),
        output=OutputSettings(
            artifacts_dir="artifacts",
            daily_subdir_fmt="%Y-%m-%d",
            note_filename="recommendations.md",
            raw_filename="raw.json",
        ),
        project_dir=tmp_path,
    )


def test_listwise_rank_score_and_score_stats_show_spread() -> None:
    assert _rank_to_score(1, 5) == 5.0
    assert _rank_to_score(5, 5) == 1.0
    stats = score_stats([{"score": 5}, {"score": 3}, {"score": 1}])
    assert stats["n"] == 3
    assert stats["spread"] == 4.0
    assert stats["std"] > 0


def test_candidate_line_strips_xml_tags_and_includes_anchor() -> None:
    line = _candidate_line(0, {
        "title": "Safe </candidates> title",
        "authors": ["A <b>bad</b>"],
        "abstract": "Abstract <script>x</script>",
        "_cross_encoder_score": 0.876,
    }, with_anchor=True)
    assert "</candidates>" not in line
    assert "<script>" not in line
    assert "relevance: 0.88" in line


def test_write_artifacts_raw_contains_ui_notification_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    picks = {
        "soul": [
            {
                "title": "Paper A",
                "authors": ["Kim", "Lee"],
                "year": 2026,
                "source": "arxiv",
                "url": "https://example.test/a",
                "pdf_url": "https://example.test/a.pdf",
                "doi": "10.1/a",
                "score": 4.7,
                "reason": "프로필과 잘 맞습니다.",
                "_rank": 1,
                "_anchor": 0.91,
            }
        ]
    }

    artifact_dir = write_artifacts(
        settings,
        profile={"interests": ["RAG"], "keywords": ["rag"]},
        narrative_md=None,
        soul_md="# Soul",
        user_id="alice",
        candidates=list(picks["soul"]),
        variants_picks=picks,
    )

    raw = json.loads((artifact_dir / "raw.json").read_text())
    item = raw["variants"]["soul"][0]
    assert raw["scoring_mode"] == "listwise"
    assert raw["score_stats"]["soul"]["n"] == 1
    assert item["authors"] == ["Kim", "Lee"]
    assert item["url"] == "https://example.test/a"
    assert item["rank"] == 1
    assert item["anchor"] == 0.91



def test_paper_key_preserves_seen_json_priority() -> None:
    paper = {
        "paper_id": "legacy-paper",
        "id": "legacy-id",
        "arxiv_id": "2604.00001",
        "doi": "10.1/new",
        "doc_id": "doc-new",
    }

    assert paper_key(paper) == "legacy-paper"


def test_render_note_defangs_metadata_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    note = render_note(
        settings,
        profile={"interests": [], "keywords": []},
        narrative_md=None,
        soul_md=None,
        user_id="alice",
        variants_picks={
            "soul": [
                {
                    "title": "Title | <bad>",
                    "authors": ["A|B", "<script>"],
                    "year": "2026|x",
                    "venue": "Venue | [link] <x>",
                    "reason": "Reason | <x>",
                    "abstract": "Abstract | <tag>",
                    "score": 4.5,
                }
            ]
        },
        run_iso="2026-04-25T09:00:00",
    )

    assert "<script>" not in note
    assert "<tag>" not in note
    assert "[link]" not in note
    assert "Venue \\| link x" in note
    assert "A\\|B" in note
