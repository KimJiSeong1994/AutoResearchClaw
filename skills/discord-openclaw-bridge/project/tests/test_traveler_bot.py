from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from discord_openclaw_bridge.config import ConfigError, TravelerBotConfig, load_traveler_config
from discord_openclaw_bridge.traveler_bot import (
    _research_verification_notice,
    build_traveler_bot,
    traveler_forum_thread_title,
)


def test_standalone_traveler_bot_registers_only_traveler_command(tmp_path: Path) -> None:
    config = TravelerBotConfig(
        discord_bot_token="traveler-token",
        guild_id=1,
        traveler_channel_id=30,
        traveler_research_queue_path=tmp_path / "research.jsonl",
        traveler_source_queue_path=tmp_path / "candidates.jsonl",
    )

    bot = build_traveler_bot(config)

    assert [command.name for command in bot.tree.get_commands()] == ["jiphyeonjeon_travel"]
    assert not bot.intents.message_content


def test_traveler_channel_allows_forum_parent_thread(tmp_path: Path) -> None:
    config = TravelerBotConfig(
        discord_bot_token="traveler-token",
        guild_id=1,
        traveler_channel_id=30,
        traveler_research_queue_path=tmp_path / "research.jsonl",
        traveler_source_queue_path=tmp_path / "candidates.jsonl",
    )
    bot = build_traveler_bot(config)
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=31, parent_id=30),
    )

    assert bot.channel_allowed(interaction) is True


def test_traveler_forum_thread_title_is_bounded() -> None:
    title = traveler_forum_thread_title({"topic": "A" * 200})

    assert title.startswith("🧭 ")
    assert len(title) == 90


def test_load_traveler_config_requires_dedicated_bot_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_GUILD_ID", "1")
    monkeypatch.setenv("DISCORD_TRAVELER_CHANNEL_ID", "2")
    monkeypatch.setenv("DISCORD_MINER_BOT_TOKEN", "miner-token")

    with pytest.raises(ConfigError, match="DISCORD_TRAVELER_BOT_TOKEN"):
        load_traveler_config()


def test_load_traveler_config_uses_dedicated_token_and_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    research_path = tmp_path / "traveler" / "research.jsonl"
    source_path = tmp_path / "traveler" / "sources.jsonl"
    monkeypatch.setenv("DISCORD_TRAVELER_BOT_TOKEN", "traveler-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "10")
    monkeypatch.setenv("DISCORD_TRAVELER_CHANNEL_ID", "30")
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH", str(research_path))
    monkeypatch.setenv("JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH", str(source_path))

    config = load_traveler_config()

    assert config.discord_bot_token == "traveler-token"
    assert config.guild_id == 10
    assert config.traveler_channel_id == 30
    assert config.traveler_research_queue_path == research_path
    assert config.traveler_source_queue_path == source_path



def test_research_verification_notice_labels_openclaw_urls_unverified() -> None:
    notice = _research_verification_notice("See https://example.com/a?utm_source=x for details")

    assert "URL 검증 안내" in notice
    assert "discovery CLI" in notice
    assert "https://example.com/a" in notice
