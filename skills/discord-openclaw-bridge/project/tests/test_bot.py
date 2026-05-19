from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

from discord_openclaw_bridge.bot import build_bot, jiphyeonjeon_agent_image_paths, render_jiphyeonjeon_agent_registry
from discord_openclaw_bridge.config import BridgeConfig


def _bridge_config(tmp_path: Path, *, enable_mention_responses: bool = False) -> BridgeConfig:
    return BridgeConfig(
        discord_bot_token="main-token",
        guild_id=1,
        allowed_channel_id=20,
        openclaw_base_url="http://127.0.0.1:18789/v1",
        openclaw_gateway_token="gateway-token",
        openclaw_model="openclaw/clawbridge",
        timeout_sec=1.0,
        enable_mention_responses=enable_mention_responses,
        max_prompt_chars=4000,
        max_response_chars=1800,
        briefing_source_path=tmp_path / "briefing.md",
        miner_channel_id=30,
        miner_intake_path=tmp_path / "intake.jsonl",
        miner_review_queue_path=tmp_path / "review.jsonl",
        miner_enable_channel_collection=True,
    )


def test_main_bridge_does_not_register_miner_command(tmp_path: Path) -> None:
    bot = build_bot(_bridge_config(tmp_path))

    command_names = [command.name for command in bot.tree.get_commands()]

    assert command_names == ["openclaw", "jiphyeonjeon_briefing", "jiphyeonjeon_agents", "openclaw_status"]
    assert "jiphyeonjeon_mine" not in command_names


def test_main_bridge_message_content_intent_ignores_miner_collection_flag(tmp_path: Path) -> None:
    bot = build_bot(_bridge_config(tmp_path, enable_mention_responses=False))

    assert not bot.intents.message_content


def test_main_bridge_message_content_intent_follows_mention_flag(tmp_path: Path) -> None:
    bot = build_bot(_bridge_config(tmp_path, enable_mention_responses=True))

    assert bot.intents.message_content


def test_main_bridge_ignores_messages_in_miner_channel(tmp_path: Path) -> None:
    bot = build_bot(_bridge_config(tmp_path, enable_mention_responses=True))
    reply = AsyncMock()
    message = SimpleNamespace(
        author=SimpleNamespace(bot=False, id=100),
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=30, typing=AsyncMock()),
        content="<@999> hello",
        mentions=[bot.user],
        reply=reply,
    )

    asyncio.run(bot.on_message(message))

    reply.assert_not_awaited()


def test_agent_registry_mentions_new_agents_and_pending_promotion() -> None:
    rendered = render_jiphyeonjeon_agent_registry()

    assert "집현전-편집자" in rendered
    assert "집현전-지도교수" in rendered
    assert "pending_future_phase" in rendered
    assert "queue 수정" in rendered
    assert "자동 승격 없음" in rendered


def test_agent_registry_image_assets_are_packaged() -> None:
    paths = jiphyeonjeon_agent_image_paths()

    names = {path.name for path in paths}
    assert "jiphyeonjeon-editor-agent.png" in names
    assert "jiphyeonjeon-advisor-agent.png" in names
    assert all(path.stat().st_size > 0 for path in paths)


def test_main_bridge_allows_threads_under_allowed_forum(tmp_path: Path) -> None:
    bot = build_bot(_bridge_config(tmp_path))
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        channel=SimpleNamespace(id=99, parent_id=20),
    )

    assert bot.channel_allowed(interaction)
