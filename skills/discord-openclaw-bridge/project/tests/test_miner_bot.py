from __future__ import annotations

from pathlib import Path

import pytest

from discord_openclaw_bridge.config import ConfigError, MinerBotConfig, load_miner_config
from discord_openclaw_bridge.miner_bot import build_miner_bot


class _FakeUser:
    def __init__(self, user_id: int, *, bot: bool = True) -> None:
        self.id = user_id
        self.bot = bot


class _FakeMessage:
    def __init__(self, *, author_id: int, author_bot: bool, mention_ids: list[int]) -> None:
        self.author = _FakeUser(author_id, bot=author_bot)
        self.mentions = [_FakeUser(user_id) for user_id in mention_ids]


def test_standalone_miner_bot_registers_only_miner_command(tmp_path: Path) -> None:
    config = MinerBotConfig(
        discord_bot_token="miner-token",
        guild_id=1,
        miner_channel_id=2,
        miner_intake_path=tmp_path / "intake.jsonl",
        miner_review_queue_path=tmp_path / "review.jsonl",
        miner_enable_channel_collection=False,
    )

    bot = build_miner_bot(config)

    assert [command.name for command in bot.tree.get_commands()] == ["jiphyeonjeon_mine"]
    assert not bot.intents.message_content


def test_standalone_miner_bot_enables_message_content_only_when_configured(tmp_path: Path) -> None:
    config = MinerBotConfig(
        discord_bot_token="miner-token",
        guild_id=1,
        miner_channel_id=2,
        miner_intake_path=tmp_path / "intake.jsonl",
        miner_review_queue_path=tmp_path / "review.jsonl",
        miner_enable_channel_collection=True,
    )

    bot = build_miner_bot(config)

    assert bot.intents.message_content


def test_load_miner_config_requires_dedicated_bot_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_GUILD_ID", "1")
    monkeypatch.setenv("DISCORD_MINER_CHANNEL_ID", "2")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "main-token")

    with pytest.raises(ConfigError, match="DISCORD_MINER_BOT_TOKEN"):
        load_miner_config()


def test_load_miner_config_uses_dedicated_token_and_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    intake_path = tmp_path / "intake" / "links.jsonl"
    review_path = tmp_path / "review" / "queue.jsonl"
    monkeypatch.setenv("DISCORD_MINER_BOT_TOKEN", "miner-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "10")
    monkeypatch.setenv("DISCORD_ALLOWED_CHANNEL_ID", "20")
    monkeypatch.setenv("JIPHYEONJEON_MINER_INTAKE_PATH", str(intake_path))
    monkeypatch.setenv("JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH", str(review_path))

    config = load_miner_config()

    assert config.discord_bot_token == "miner-token"
    assert config.guild_id == 10
    assert config.miner_channel_id == 20
    assert config.miner_intake_path == intake_path
    assert config.miner_review_queue_path == review_path
    assert config.traveler_research_queue_path is not None
    assert config.traveler_source_queue_path is not None


def test_miner_bot_allows_only_mentioned_traveler_bot_relay(tmp_path: Path) -> None:
    config = MinerBotConfig(
        discord_bot_token="miner-token",
        guild_id=1,
        miner_channel_id=2,
        miner_intake_path=tmp_path / "intake.jsonl",
        miner_review_queue_path=tmp_path / "review.jsonl",
        miner_enable_channel_collection=False,
        traveler_client_id=99,
    )
    bot = build_miner_bot(config)
    bot._connection.user = _FakeUser(42)  # type: ignore[attr-defined]

    assert bot.traveler_relay_allowed(_FakeMessage(author_id=99, author_bot=True, mention_ids=[42]))
    assert not bot.traveler_relay_allowed(_FakeMessage(author_id=99, author_bot=True, mention_ids=[]))
    assert not bot.traveler_relay_allowed(_FakeMessage(author_id=100, author_bot=True, mention_ids=[42]))
    assert not bot.traveler_relay_allowed(_FakeMessage(author_id=99, author_bot=False, mention_ids=[42]))
