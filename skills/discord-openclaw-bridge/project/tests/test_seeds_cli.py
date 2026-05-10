"""Tests for discord_openclaw_bridge.seeds_cli -- design doc sec 3.4."""
from __future__ import annotations

import json
import os
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

    status_path = tmp_path / "status.json"
    main(
        [
            "--seeds-path", str(seeds_path),
            "--state-path", str(state_path),
            "--intake-path", str(intake_path),
            "--review-queue-path", str(review_path),
            "--status-path", str(status_path),
        ]
    )

    assert len(captured_calls) == 1
    assert state_path.exists()
    assert status_path.exists(), "status JSON must be written after default-mode run"
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["seeds_total"] == 1
    assert payload["total_accepted"] == 5
    assert payload["seeds_with_errors"] == 0
    assert payload["summaries"][0]["seed_url"] == _NATURE_SEED["url"]
    assert payload["summaries"][0]["accepted"] == 5
    assert "run_at" in payload and payload["run_at"].endswith("Z")


def test_cli_status_file_records_errors_and_cooldown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Status JSON must surface error and cooldown skip counts for the reporter."""
    import discord_openclaw_bridge.seeds_cli as cli_module
    from discord_openclaw_bridge.seeds import SeedRunSummary

    seeds_path = tmp_path / "seeds.json"
    status_path = tmp_path / "status.json"
    other_seed = {**_NATURE_SEED, "url": "https://www.alphaxiv.org/"}
    _write_seeds(seeds_path, [_NATURE_SEED, other_seed])

    def _mock_expand_seeds(**_: object) -> list[SeedRunSummary]:
        return [
            SeedRunSummary(
                seed_url=_NATURE_SEED["url"],
                expanded_count=0,
                accepted=0,
                duplicate=0,
                rejected=0,
                skipped_cooldown=False,
                error="empty_expansion",
            ),
            SeedRunSummary(
                seed_url=other_seed["url"],
                expanded_count=0,
                accepted=0,
                duplicate=0,
                rejected=0,
                skipped_cooldown=True,
            ),
        ]

    monkeypatch.setattr(cli_module, "expand_seeds", _mock_expand_seeds)

    # empty_expansion is now a transient warning — main() must NOT raise
    # SystemExit so the cron runner keeps going and posts the Discord report.
    main(
        [
            "--seeds-path", str(seeds_path),
            "--state-path", str(tmp_path / "state.json"),
            "--intake-path", str(tmp_path / "intake.jsonl"),
            "--review-queue-path", str(tmp_path / "review.jsonl"),
            "--status-path", str(status_path),
        ]
    )

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["seeds_total"] == 2
    # empty_expansion is a transient warning, NOT a real error — it must move
    # the count out of seeds_with_errors so the Discord report uses ⚠️ instead
    # of 🚨 (post-review fix H2).
    assert payload["seeds_with_errors"] == 0
    assert payload["seeds_with_warnings"] == 1
    assert payload["seeds_skipped_cooldown"] == 1
    assert payload["total_accepted"] == 0
    assert any(s.get("error") == "empty_expansion" for s in payload["summaries"])


def test_cli_real_error_kept_in_seeds_with_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-transient error tag must still raise 🚨 (seeds_with_errors > 0)."""
    import discord_openclaw_bridge.seeds_cli as cli_module
    from discord_openclaw_bridge.seeds import SeedRunSummary

    seeds_path = tmp_path / "seeds.json"
    status_path = tmp_path / "status.json"
    _write_seeds(seeds_path, [_NATURE_SEED])

    def _mock_expand_seeds(**_: object) -> list[SeedRunSummary]:
        return [
            SeedRunSummary(
                seed_url=_NATURE_SEED["url"],
                expanded_count=0,
                accepted=0,
                duplicate=0,
                rejected=0,
                skipped_cooldown=False,
                error="parser_crashed",
            )
        ]

    monkeypatch.setattr(cli_module, "expand_seeds", _mock_expand_seeds)

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "--seeds-path", str(seeds_path),
                "--state-path", str(tmp_path / "state.json"),
                "--intake-path", str(tmp_path / "intake.jsonl"),
                "--review-queue-path", str(tmp_path / "review.jsonl"),
                "--status-path", str(status_path),
            ]
        )
    assert excinfo.value.code == 1

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["seeds_with_errors"] == 1
    assert payload["seeds_with_warnings"] == 0


def test_cli_transient_warning_does_not_exit_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """empty_expansion warnings must not break the cron — the next firing retries."""
    import discord_openclaw_bridge.seeds_cli as cli_module
    from discord_openclaw_bridge.seeds import SeedRunSummary

    seeds_path = tmp_path / "seeds.json"
    _write_seeds(seeds_path, [_NATURE_SEED])

    def _mock_expand_seeds(**_: object) -> list[SeedRunSummary]:
        return [
            SeedRunSummary(
                seed_url=_NATURE_SEED["url"],
                expanded_count=0,
                accepted=0,
                duplicate=0,
                rejected=0,
                skipped_cooldown=False,
                error="empty_expansion",
            )
        ]

    monkeypatch.setattr(cli_module, "expand_seeds", _mock_expand_seeds)

    # Should return cleanly (no SystemExit), so the cron runner continues to
    # post the Discord report and keeps last_seen guarded for the retry.
    main(
        [
            "--seeds-path", str(seeds_path),
            "--state-path", str(tmp_path / "state.json"),
            "--intake-path", str(tmp_path / "intake.jsonl"),
            "--review-queue-path", str(tmp_path / "review.jsonl"),
            "--status-path", str(tmp_path / "status.json"),
        ]
    )


# ---------------------------------------------------------------------------
# Production default-path alignment (review fix A3)
# ---------------------------------------------------------------------------


def test_cli_resolves_production_intake_paths_from_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default intake / review-queue paths must align with miner_bot / review_cli.

    Without this contract the cron-driven seed expander writes to a different
    JSONL than the slash-command miner reads from, so seed records become
    invisible to the Claw review pipeline.
    """
    monkeypatch.delenv("JIPHYEONJEON_MINER_INTAKE_PATH", raising=False)
    monkeypatch.delenv("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", raising=False)
    monkeypatch.delenv("MINER_SEEDS_STATUS_PATH", raising=False)

    import discord_openclaw_bridge.seeds_cli as cli_module
    defaults = cli_module._resolve_default_paths()

    expected_intake = Path.home() / ".openclaw" / "workspace" / "intake" / "jiphyeonjeon-miner" / "links.jsonl"
    expected_review = Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-claw" / "link-review-queue.jsonl"
    expected_status = Path.home() / ".openclaw" / "workspace" / "state" / "miner-seeds-last-status.json"

    assert defaults["intake"] == expected_intake
    assert defaults["review_queue"] == expected_review
    assert defaults["status"] == expected_status


def test_cli_defaults_honor_production_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The production env vars used by miner_bot/config.py must override defaults."""
    custom_intake = tmp_path / "custom-intake.jsonl"
    custom_review = tmp_path / "custom-review.jsonl"
    custom_status = tmp_path / "custom-status.json"

    monkeypatch.setenv("JIPHYEONJEON_MINER_INTAKE_PATH", str(custom_intake))
    monkeypatch.setenv("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", str(custom_review))
    monkeypatch.setenv("MINER_SEEDS_STATUS_PATH", str(custom_status))

    import discord_openclaw_bridge.seeds_cli as cli_module
    defaults = cli_module._resolve_default_paths()

    assert defaults["intake"] == custom_intake
    assert defaults["review_queue"] == custom_review
    assert defaults["status"] == custom_status


def test_cli_loads_dotenv_before_resolving_default_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path in project/.env must reach argparse defaults via main()'s dotenv pre-load.

    Regression for review fix H1: previously path resolution happened at
    module import time, so JIPHYEONJEON_MINER_* values written into
    project/.env were silently ignored — the slash-command miner saw them
    (config.py loads dotenv) but seeds_cli did not, leaving cron records
    in an orphan path the review CLI never reads.
    """
    custom_intake = tmp_path / "from-dotenv-intake.jsonl"
    monkeypatch.delenv("JIPHYEONJEON_MINER_INTAKE_PATH", raising=False)

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    env_file = project_dir / ".env"
    env_file.write_text(
        f"JIPHYEONJEON_MINER_INTAKE_PATH={custom_intake}\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(project_dir)

    seeds_path = tmp_path / "seeds.json"
    _write_seeds(seeds_path, [])  # empty → main() returns after the load_seeds gate

    import discord_openclaw_bridge.seeds_cli as cli_module
    main(["--seeds-path", str(seeds_path)])

    # Verify the dotenv value reached the env at load time.
    assert os.environ.get("JIPHYEONJEON_MINER_INTAKE_PATH") == str(custom_intake)
    # And that a fresh resolution after the dotenv load produces the dotenv value.
    defaults = cli_module._resolve_default_paths()
    assert defaults["intake"] == custom_intake
