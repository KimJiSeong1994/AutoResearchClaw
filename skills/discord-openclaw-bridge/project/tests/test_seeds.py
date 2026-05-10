"""Tests for discord_openclaw_bridge.seeds -- design doc sec 7.1."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import discord_openclaw_bridge.seeds as seeds_module
from discord_openclaw_bridge.article_metadata import ArticleMetadata
from discord_openclaw_bridge.seeds import (
    SeedEntry,
    SeedRunSummary,
    expand_seeds,
    load_last_seen,
    load_seeds,
    save_last_seen,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NATURE_SEED_URL = "https://nature.com/nature/articles?type=article"
_NATURE_ARTICLE_URL = "https://nature.com/articles/s41586-024-00001-0"
_NOW = datetime(2026, 5, 9, 21, 0, 0, tzinfo=timezone.utc)
_ISO_NOW = "2026-05-09T21:00:00+00:00"


def _seeds_json(entries: list[dict]) -> str:
    return json.dumps({"seeds": entries})


def _make_seed(
    url: str = _NATURE_SEED_URL,
    label: str = "Nature",
    cooldown_hours: int = 24,
    enabled: bool = True,
    max_links: int | None = None,
) -> SeedEntry:
    return SeedEntry(
        url=url,
        label=label,
        cooldown_hours=cooldown_hours,
        enabled=enabled,
        max_links=max_links,
    )


def _academic_meta(url: str) -> ArticleMetadata:
    """Metadata with academic terms that pass record_miner_link's relevance filter."""
    return ArticleMetadata(
        url=url,
        title="Deep learning research paper on model evaluation benchmark",
        summary="We present a novel framework for evaluating LLM reasoning.",
        published_at="2026-05-01",
    )


def _blank_meta(url: str) -> ArticleMetadata:
    return ArticleMetadata(url=url)


# ---------------------------------------------------------------------------
# load_seeds
# ---------------------------------------------------------------------------


def test_load_seeds_happy_path(tmp_path: Path) -> None:
    f = tmp_path / "seeds.json"
    f.write_text(
        _seeds_json(
            [
                {
                    "url": _NATURE_SEED_URL,
                    "label": "Nature research articles",
                    "cooldown_hours": 24,
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    result = load_seeds(f)

    assert len(result) == 1
    assert result[0].url == _NATURE_SEED_URL
    assert result[0].label == "Nature research articles"
    assert result[0].cooldown_hours == 24
    assert result[0].enabled is True
    assert result[0].max_links is None


def test_load_seeds_missing_file(tmp_path: Path) -> None:
    assert load_seeds(tmp_path / "nonexistent.json") == []


def test_load_seeds_invalid_url_skipped(tmp_path: Path) -> None:
    f = tmp_path / "seeds.json"
    f.write_text(
        _seeds_json(
            [
                {"url": "not-a-url", "label": "Bad"},
                {"url": _NATURE_SEED_URL, "label": "Good"},
            ]
        ),
        encoding="utf-8",
    )

    result = load_seeds(f)

    assert len(result) == 1
    assert result[0].label == "Good"


def test_load_seeds_invalid_cooldown_skipped(tmp_path: Path) -> None:
    f = tmp_path / "seeds.json"
    f.write_text(
        _seeds_json(
            [
                {"url": _NATURE_SEED_URL, "label": "Zero", "cooldown_hours": 0},
                {"url": _NATURE_SEED_URL, "label": "Negative", "cooldown_hours": -5},
                {"url": _NATURE_SEED_URL, "label": "String", "cooldown_hours": "24"},
                {
                    "url": "https://arxiv.org/abs/2301.00000",
                    "label": "Good",
                    "cooldown_hours": 12,
                },
            ]
        ),
        encoding="utf-8",
    )

    result = load_seeds(f)

    assert len(result) == 1
    assert result[0].label == "Good"


def test_load_seeds_invalid_enabled_type_skipped(tmp_path: Path) -> None:
    f = tmp_path / "seeds.json"
    f.write_text(
        _seeds_json(
            [
                {"url": _NATURE_SEED_URL, "label": "String bool", "enabled": "yes"},
                {"url": _NATURE_SEED_URL, "label": "Int bool", "enabled": 1},
                {
                    "url": "https://arxiv.org/abs/2301.00001",
                    "label": "Good",
                    "enabled": False,
                },
            ]
        ),
        encoding="utf-8",
    )

    result = load_seeds(f)

    assert len(result) == 1
    assert result[0].label == "Good"
    assert result[0].enabled is False


# ---------------------------------------------------------------------------
# load_last_seen / save_last_seen
# ---------------------------------------------------------------------------


def test_load_last_seen_missing_file(tmp_path: Path) -> None:
    assert load_last_seen(tmp_path / "nonexistent.json") == {}


def test_load_last_seen_corrupt_file(tmp_path: Path) -> None:
    f = tmp_path / "state.json"
    f.write_text("not valid json {{{{", encoding="utf-8")
    assert load_last_seen(f) == {}


def test_save_last_seen_atomic_write(tmp_path: Path) -> None:
    """save_last_seen uses tmp+rename; no partial file remains."""
    state_file = tmp_path / "subdir" / "last-seen.json"
    mapping = {_NATURE_SEED_URL: _ISO_NOW}

    save_last_seen(state_file, mapping)

    assert state_file.exists()
    assert not state_file.with_suffix(".tmp").exists()
    assert json.loads(state_file.read_text(encoding="utf-8")) == mapping


# ---------------------------------------------------------------------------
# expand_seeds
# ---------------------------------------------------------------------------


def test_expand_seeds_disabled_seed_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _make_seed(enabled=False)
    expand_called: list[str] = []
    monkeypatch.setattr(
        seeds_module, "expand_collection_links", lambda url: expand_called.append(url) or []
    )

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=tmp_path / "state.json",
        now=_NOW,
    )

    assert len(summaries) == 1
    s = summaries[0]
    assert s.skipped_cooldown is False
    assert s.expanded_count == 0
    assert s.error is None
    assert expand_called == []


def test_expand_seeds_cooldown_active_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed = _make_seed(cooldown_hours=24)
    last_seen_ts = (_NOW - timedelta(hours=23, minutes=59)).isoformat()
    state_path = tmp_path / "state.json"
    save_last_seen(state_path, {seed.url: last_seen_ts})

    expand_called: list[str] = []
    monkeypatch.setattr(
        seeds_module, "expand_collection_links", lambda url: expand_called.append(url) or []
    )

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=state_path,
        now=_NOW,
    )

    assert summaries[0].skipped_cooldown is True
    assert expand_called == []


def test_expand_seeds_cooldown_exactly_elapsed_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exactly cooldown_hours elapsed => NOT skipped (elapsed >= cooldown)."""
    seed = _make_seed(cooldown_hours=24)
    last_seen_ts = (_NOW - timedelta(hours=24)).isoformat()
    state_path = tmp_path / "state.json"
    save_last_seen(state_path, {seed.url: last_seen_ts})

    monkeypatch.setattr(seeds_module, "expand_collection_links", lambda url: [])
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=state_path,
        now=_NOW,
        fetch_metadata=_blank_meta,
    )

    assert summaries[0].skipped_cooldown is False


def test_expand_seeds_first_run_accept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """First run with no last_seen file processes the seed and records articles."""
    seed = _make_seed()
    article_url = _NATURE_ARTICLE_URL
    monkeypatch.setattr(seeds_module, "expand_collection_links", lambda url: [article_url])
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=tmp_path / "state.json",
        now=_NOW,
        fetch_metadata=_academic_meta,  # academic terms so record_miner_link accepts
    )

    assert len(summaries) == 1
    s = summaries[0]
    assert s.expanded_count == 1
    assert s.accepted == 1
    assert s.skipped_cooldown is False
    assert s.error is None


def test_expand_seeds_collection_fail_error_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """expand_collection_links raising => SeedRunSummary.error set; next seed continues."""
    bad_seed = _make_seed(url=_NATURE_SEED_URL, label="Bad")
    good_seed = _make_seed(url="https://arxiv.org/abs/2301.00002", label="Good")

    def _patched_expand(url: str) -> list[str]:
        if "nature" in url:
            raise RuntimeError("network error")
        return []

    monkeypatch.setattr(seeds_module, "expand_collection_links", _patched_expand)
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    summaries = expand_seeds(
        seeds=[bad_seed, good_seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=tmp_path / "state.json",
        now=_NOW,
        fetch_metadata=_academic_meta,
    )

    assert len(summaries) == 2
    bad_s = next(s for s in summaries if "nature" in s.seed_url)
    good_s = next(s for s in summaries if "arxiv" in s.seed_url)
    assert bad_s.error == "network error"
    assert bad_s.expanded_count == 0
    assert good_s.error is None


def test_expand_seeds_article_metadata_fail_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-article metadata fetch failure => blank metadata used; seed overall not failed."""
    seed = _make_seed()
    article_urls = [
        "https://nature.com/articles/s41586-024-00001-0",
        "https://nature.com/articles/s41586-024-00002-0",
    ]
    monkeypatch.setattr(seeds_module, "expand_collection_links", lambda url: article_urls)
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    call_n = 0

    def _flaky(url: str) -> ArticleMetadata:
        nonlocal call_n
        call_n += 1
        if call_n == 1:
            raise ValueError("fetch failed")
        # Second article gets academic metadata => accepted
        return ArticleMetadata(url=url, title="Research paper on model evaluation benchmark")

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=tmp_path / "state.json",
        now=_NOW,
        fetch_metadata=_flaky,
    )

    s = summaries[0]
    # Both articles processed (no whole-seed failure)
    assert s.expanded_count == 2
    # nature.com is in _ACADEMIC_TECH_HOST_HINTS, so both URLs pass eligibility
    # regardless of metadata content (blank meta on article 1 is still accepted).
    assert s.accepted == 2
    assert s.rejected == 0
    assert s.error is None


def test_expand_seeds_last_seen_persisted_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful seed run (1+ links found), last_seen is written with current timestamp."""
    seed = _make_seed()
    state_path = tmp_path / "state.json"
    # Return one article so the recognised-collection empty-guard doesn't trigger.
    monkeypatch.setattr(
        seeds_module, "expand_collection_links", lambda url: [_NATURE_ARTICLE_URL]
    )
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=state_path,
        now=_NOW,
        fetch_metadata=_blank_meta,
    )

    assert state_path.exists()
    loaded = load_last_seen(state_path)
    assert seed.url in loaded
    stored_ts = datetime.fromisoformat(loaded[seed.url]).astimezone(timezone.utc)
    assert stored_ts == _NOW.replace(microsecond=0)


def test_expand_seeds_last_seen_not_updated_on_collection_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """last_seen is NOT updated when collection expansion fails."""
    seed = _make_seed()
    state_path = tmp_path / "state.json"

    def _fail(url: str) -> list[str]:
        raise RuntimeError("fail")

    monkeypatch.setattr(seeds_module, "expand_collection_links", _fail)

    expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=state_path,
        now=_NOW,
        fetch_metadata=_blank_meta,
    )

    assert not state_path.exists()


def test_expand_seeds_max_links_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """seed.max_links limits the number of articles processed."""
    seed = _make_seed(max_links=2)
    all_links = [f"https://nature.com/articles/s41586-024-0000{i}-0" for i in range(5)]
    monkeypatch.setattr(seeds_module, "expand_collection_links", lambda url: all_links)
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=tmp_path / "state.json",
        now=_NOW,
        fetch_metadata=_academic_meta,  # academic terms => accepted
    )

    assert summaries[0].expanded_count == 2
    assert summaries[0].accepted == 2


# ---------------------------------------------------------------------------
# F1: silent-fail guard — empty_expansion
# ---------------------------------------------------------------------------


def test_expand_seeds_collection_empty_marks_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recognised collection seed returning [] => error='empty_expansion', last_seen NOT updated."""
    seed = _make_seed(url=_NATURE_SEED_URL)
    state_path = tmp_path / "state.json"

    # expand_collection_links returns [] (simulates network error / selector drift)
    monkeypatch.setattr(seeds_module, "expand_collection_links", lambda url: [])
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    summaries = expand_seeds(
        seeds=[seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=state_path,
        now=_NOW,
        fetch_metadata=_blank_meta,
    )

    assert len(summaries) == 1
    s = summaries[0]
    assert s.error == "empty_expansion"
    assert s.expanded_count == 0
    assert s.skipped_cooldown is False
    # last_seen must NOT be updated so the next cron run retries
    assert not state_path.exists()


def test_expand_seeds_non_collection_seed_zero_links_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-collection seed URL returning [] from expand_collection_links is NOT an error."""
    # arxiv.org/abs/... is a direct article URL, not a recognised collection pattern.
    non_collection_seed = _make_seed(url="https://arxiv.org/abs/2301.00000")
    state_path = tmp_path / "state.json"

    # expand_collection_links returns [] for non-collection URLs (expected behaviour)
    monkeypatch.setattr(seeds_module, "expand_collection_links", lambda url: [])
    monkeypatch.setattr(seeds_module.time, "sleep", lambda _: None)

    summaries = expand_seeds(
        seeds=[non_collection_seed],
        intake_path=tmp_path / "intake.jsonl",
        review_queue_path=tmp_path / "review.jsonl",
        state_path=state_path,
        now=_NOW,
        fetch_metadata=_blank_meta,
    )

    assert len(summaries) == 1
    s = summaries[0]
    assert s.error is None  # NOT an error — expected for non-collection seed
    assert s.expanded_count == 0
    assert s.skipped_cooldown is False
    # last_seen IS updated (seed ran OK, just found 0 articles)
    assert state_path.exists()
