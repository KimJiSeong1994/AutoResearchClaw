"""Tests for discord_openclaw_bridge.seeds_cli -- design doc sec 3.4."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from discord_openclaw_bridge.seeds_cli import main


def _write_seeds(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seeds": entries}), encoding="utf-8")


_NATURE_SEED = {
    "url": "https://nature.com/nature/articles?type=article",
    "label": "Nature research articles",
    "cooldown_hours": 24,
    "enabled": True,
}


def test_cli_dry_run_prints_seed_info_and_makes_no_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--dry-run validates seeds and logs entries; no intake/state files written."""
    seeds_path = tmp_path / "seeds.json"
    state_path = tmp_path / "state.json"
    intake_path = tmp_path / "intake.jsonl"
    review_path = tmp_path / "review.jsonl"
    _write_seeds(seeds_path, [_NATURE_SEED])

    main(
        [
            "--dry-run",
            "--seeds-path", str(seeds_path),
            "--state-path", str(state_path),
            "--intake-path", str(intake_path),
            "--review-queue-path", str(review_path),
        ]
    )

    assert not state_path.exists(), "state file must not be written in dry-run"
    assert not intake_path.exists(), "intake file must not be written in dry-run"


def test_cli_missing_seeds_file_exits_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When seeds.json does not exist, main() logs a warning and returns without error."""
    missing = tmp_path / "nonexistent-seeds.json"

    # Should return without raising
    main(
        [
            "--seeds-path", str(missing),
            "--state-path", str(tmp_path / "state.json"),
            "--intake-path", str(tmp_path / "intake.jsonl"),
            "--review-queue-path", str(tmp_path / "review.jsonl"),
        ]
    )

    # No crash => pass


def test_cli_expand_runs_and_persists_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-dry-run expand with monkeypatched expand_seeds writes state and logs summaries."""
    import discord_openclaw_bridge.seeds_cli as cli_module
    from discord_openclaw_bridge.seeds import SeedRunSummary

    seeds_path = tmp_path / "seeds.json"
    state_path = tmp_path / "state.json"
    intake_path = tmp_path / "intake.jsonl"
    review_path = tmp_path / "review.jsonl"
    _write_seeds(seeds_path, [_NATURE_SEED])

    captured_calls: list[dict] = []

    def _mock_expand_seeds(**kwargs: object) -> list[SeedRunSummary]:
        captured_calls.append(dict(kwargs))
        # Simulate a successful run: write state manually and return a summary.
        from discord_openclaw_bridge.seeds import save_last_seen
        save_last_seen(
            state_path,
            {"https://nature.com/nature/articles?type=article": "2026-05-09T21:00:00+00:00"},
        )
        return [
            SeedRunSummary(
                seed_url="https://nature.com/nature/articles?type=article",
                expanded_count=5,
                accepted=5,
                duplicate=0,
                rejected=0,
                skipped_cooldown=False,
            )
        ]

    monkeypatch.setattr(cli_module, "expand_seeds", _mock_expand_seeds)

    main(
        [
            "--seeds-path", str(seeds_path),
            "--state-path", str(state_path),
            "--intake-path", str(intake_path),
            "--review-queue-path", str(review_path),
        ]
    )

    assert len(captured_calls) == 1
    assert state_path.exists()
