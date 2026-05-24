from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from discord_openclaw_bridge.config import ConfigError, ReporterBotConfig, load_reporter_config
from discord_openclaw_bridge.reporter_bot import build_reporter_bot, render_draft_preview, render_reporter_status


def _draft(tmp_path: Path) -> Path:
    path = tmp_path / "draft.md"
    path.write_text(
        """---
title: 집현전 기자 앱 테스트
excerpt: Discord 앱 미리보기 검증입니다.
author: 집현전 팀
tags: [기술, 검증]
---
# 집현전 기자 앱 테스트

공개 근거는 https://example.com/source 와 https://research.example.org/paper 에서 확인합니다.
""",
        encoding="utf-8",
    )
    return path


def test_standalone_reporter_bot_registers_reporter_commands(tmp_path: Path) -> None:
    config = ReporterBotConfig(
        discord_bot_token="reporter-token",
        guild_id=1,
        reporter_channel_id=30,
        reporter_draft_dir=tmp_path,
    )

    bot = build_reporter_bot(config)

    assert [command.name for command in bot.tree.get_commands()] == [
        "jiphyeonjeon_reporter_status",
        "jiphyeonjeon_reporter_preview",
    ]
    assert not bot.intents.message_content


def test_reporter_channel_allows_forum_parent_thread(tmp_path: Path) -> None:
    config = ReporterBotConfig(
        discord_bot_token="reporter-token",
        guild_id=1,
        reporter_channel_id=30,
        reporter_draft_dir=tmp_path,
    )
    bot = build_reporter_bot(config)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=31, parent_id=30),
    )

    assert bot.channel_allowed(interaction) is True


def test_load_reporter_config_requires_dedicated_bot_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_GUILD_ID", "1")
    monkeypatch.setenv("DISCORD_REPORTER_CHANNEL_ID", "2")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "main-token")

    with pytest.raises(ConfigError, match="DISCORD_REPORTER_BOT_TOKEN"):
        load_reporter_config()


def test_load_reporter_config_uses_dedicated_token_and_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    draft_dir = tmp_path / "drafts"
    monkeypatch.setenv("DISCORD_REPORTER_BOT_TOKEN", "reporter-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "10")
    monkeypatch.setenv("DISCORD_REPORTER_CHANNEL_ID", "30")
    monkeypatch.setenv("JIPHYEONJEON_REPORTER_DRAFT_DIR", str(draft_dir))

    config = load_reporter_config()

    assert config.discord_bot_token == "reporter-token"
    assert config.guild_id == 10
    assert config.reporter_channel_id == 30
    assert config.reporter_draft_dir == draft_dir


def test_reporter_status_names_publication_boundary(tmp_path: Path) -> None:
    text = render_reporter_status(
        ReporterBotConfig(
            discord_bot_token="reporter-token",
            guild_id=1,
            reporter_channel_id=30,
            reporter_draft_dir=tmp_path,
        )
    )

    assert "집현전-기자 앱 활성화" in text
    assert "approval-id" in text


def test_reporter_draft_preview_uses_latest_markdown_and_public_sources(tmp_path: Path) -> None:
    _draft(tmp_path)
    config = ReporterBotConfig(
        discord_bot_token="reporter-token",
        guild_id=1,
        reporter_channel_id=30,
        reporter_draft_dir=tmp_path,
    )

    preview = render_draft_preview(config)

    assert "집현전 기자 앱 테스트" in preview
    assert "공개 URL 근거 수" in preview
    assert "approval-id" in preview
