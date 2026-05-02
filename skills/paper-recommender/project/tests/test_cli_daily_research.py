from __future__ import annotations

from pathlib import Path

import pytest

from paper_recommender import cli


def test_daily_research_subcommand_parses(monkeypatch) -> None:
    """The CLI accepts ``daily-research --dry-run`` without raising."""

    captured: dict = {}

    async def stub_run(config_path, dry_run):
        captured["config"] = config_path
        captured["dry_run"] = dry_run
        from paper_recommender.daily_research import RunResult

        return RunResult(
            candidate_count=0,
            cluster_count=0,
            deep_success_count=0,
            note_markdown="",
        )

    monkeypatch.setattr(cli, "run_daily_research", stub_run)

    rc = cli.main(["--config", "/tmp/cfg.yaml", "daily-research", "--dry-run"])
    assert rc == 0
    assert captured["config"] == Path("/tmp/cfg.yaml")
    assert captured["dry_run"] is True


def test_daily_research_subcommand_default_not_dry_run(monkeypatch) -> None:
    captured: dict = {}

    async def stub_run(config_path, dry_run):
        captured["dry_run"] = dry_run
        from paper_recommender.daily_research import RunResult

        return RunResult()

    monkeypatch.setattr(cli, "run_daily_research", stub_run)
    rc = cli.main(["daily-research"])
    assert rc == 0
    assert captured["dry_run"] is False


def test_unknown_subcommand_fails() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["this-does-not-exist"])
    assert exc_info.value.code != 0
