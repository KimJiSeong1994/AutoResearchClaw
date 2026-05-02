from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from paper_recommender.state import StateStore


def test_empty_state_is_not_recently_seen(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    assert store.is_recently_deep_seen("anything", 7) is False


def test_record_and_is_recently_deep_seen(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.record_deep_seen(["transformer attention", "graph neural networks"])
    assert store.is_recently_deep_seen("transformer attention", 7) is True
    assert store.is_recently_deep_seen("graph neural networks", 7) is True
    assert store.is_recently_deep_seen("not seen", 7) is False


def test_expired_deep_seen_returns_false(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    # Manually inject an old date
    old_date = (date.today() - timedelta(days=30)).isoformat()
    import json
    store.deep_seen_path.write_text(json.dumps({"old topic": old_date}))
    assert store.is_recently_deep_seen("old topic", 7) is False
    assert store.is_recently_deep_seen("old topic", 60) is True


def test_record_does_not_overwrite_other_entries(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.record_deep_seen(["a", "b"])
    store.record_deep_seen(["c"])
    assert store.is_recently_deep_seen("a", 7)
    assert store.is_recently_deep_seen("b", 7)
    assert store.is_recently_deep_seen("c", 7)


def test_record_skips_empty_keys(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.record_deep_seen(["", "real topic", ""])
    seen = store.load_deep_seen()
    assert "real topic" in seen
    assert "" not in seen


def test_gc_deep_seen_prunes_old_entries(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    import json
    today = date.today().isoformat()
    very_old = (date.today() - timedelta(days=100)).isoformat()
    store.deep_seen_path.write_text(json.dumps({
        "fresh": today,
        "stale": very_old,
    }))
    # cooldown=7 means cutoff is 14 days ago; "stale" is 100 days old → pruned.
    store.gc_deep_seen(cooldown_days=7)
    remaining = store.load_deep_seen()
    assert "fresh" in remaining
    assert "stale" not in remaining


def test_gc_handles_malformed_dates(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    import json
    store.deep_seen_path.write_text(json.dumps({
        "good": date.today().isoformat(),
        "bad": "not a date",
    }))
    store.gc_deep_seen(cooldown_days=7)
    remaining = store.load_deep_seen()
    assert "good" in remaining
    assert "bad" not in remaining


def test_record_empty_list_is_noop(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state")
    store.record_deep_seen([])
    assert not store.deep_seen_path.exists() or store.load_deep_seen() == {}


def test_deep_seen_separate_from_paper_seen(tmp_path: Path) -> None:
    """Paper-seen and deep-seen must not share storage."""
    store = StateStore(tmp_path / "state")
    store.record_seen(["paper-id-1"])
    store.record_deep_seen(["cluster-key-1"])
    assert store.is_recently_seen("paper-id-1", 30)
    assert not store.is_recently_seen("cluster-key-1", 30)
    assert store.is_recently_deep_seen("cluster-key-1", 30)
    assert not store.is_recently_deep_seen("paper-id-1", 30)
    # Storage paths must differ
    assert store.deep_seen_path != store.seen_path
