from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import discord
from discord import app_commands

from .config import ConfigError, TravelerBotConfig, load_traveler_config
from .miner import DiscordLinkMetadata
from .openclaw import OpenClawClient
from .traveler import (
    TravelerResearchRequest,
    record_research_request,
    render_research_pending_notice,
    render_research_prompt,
    render_research_request_ack,
)

LOG = logging.getLogger("discord_jiphyeonjeon_traveler")


class JiphyeonjeonTravelerBot(discord.Client):
    """Standalone Discord bot for 집현전-여행자 deep source discovery requests."""

    def __init__(self, config: TravelerBotConfig):
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.openclaw = OpenClawClient(
            base_url=config.openclaw_base_url,
            token=config.openclaw_gateway_token,
            model=config.openclaw_model,
            timeout_sec=config.timeout_sec,
        )

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        LOG.info("synced %s traveler commands for guild=%s", len(synced), self.config.guild_id)

    async def on_ready(self) -> None:
        LOG.info(
            "ready user=%s guild=%s traveler_channel=%s",
            self.user,
            self.config.guild_id,
            self.config.traveler_channel_id,
        )

    def channel_allowed(self, interaction: discord.Interaction) -> bool:
        channel = interaction.channel
        channel_id = getattr(channel, "id", None)
        parent_id = getattr(channel, "parent_id", None)
        parent = getattr(channel, "parent", None)
        if parent_id is None and parent is not None:
            parent_id = getattr(parent, "id", None)
        return bool(
            interaction.guild is not None
            and interaction.guild.id == self.config.guild_id
            and channel is not None
            and (channel_id == self.config.traveler_channel_id or parent_id == self.config.traveler_channel_id)
        )


async def _travel_command(
    interaction: discord.Interaction,
    topic: str,
    scope: str | None = None,
    min_sources_to_review: int = 20,
    note: str | None = None,
) -> None:
    bot = interaction.client
    assert isinstance(bot, JiphyeonjeonTravelerBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("집현전-여행자 리서치 요청은 지정된 여행자 포럼에서만 사용할 수 있습니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        record = record_research_request(
            TravelerResearchRequest(
                topic=topic,
                scope=scope,
                min_sources_to_review=min_sources_to_review,
                requester_note=note,
            ),
            queue_path=bot.config.traveler_research_queue_path,
            candidate_queue_path=bot.config.traveler_source_queue_path,
            discord=DiscordLinkMetadata(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id if interaction.user else None,
            ),
        )
    except ValueError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except Exception:
        LOG.exception("traveler slash request failed guild=%s channel=%s", interaction.guild_id, interaction.channel_id)
        await interaction.followup.send("집현전-여행자 리서치 요청 등록에 실패했습니다. 운영 로그를 확인해 주세요.", ephemeral=True)
        return
    ack = render_research_request_ack(record)
    forum_thread = await publish_traveler_forum_record(bot, record, ack)
    if forum_thread is not None:
        asyncio.create_task(publish_traveler_deep_research(bot, forum_thread, record))
    suffix = f"\n포럼 게시글: {forum_thread.mention}" if forum_thread is not None else ""
    await interaction.followup.send(f"{ack}{suffix}\n심층 리서치 결과는 포럼 스레드에 이어서 게시됩니다.", ephemeral=True)


def _trim_discord(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n…(truncated)"


async def publish_traveler_deep_research(
    bot: JiphyeonjeonTravelerBot,
    thread: discord.Thread,
    record: dict[str, Any],
) -> None:
    await thread.send(
        _trim_discord(render_research_pending_notice(record), bot.config.max_response_chars),
        allowed_mentions=discord.AllowedMentions.none(),
    )
    if not bot.config.openclaw_gateway_token:
        await thread.send(
            "OpenClaw gateway token is not configured, so only the research request was recorded.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    try:
        research = await bot.openclaw.chat(
            render_research_prompt(record),
            max_tokens=900,
            timeout_sec=min(bot.config.timeout_sec, 45),
        )
    except Exception:
        LOG.exception("traveler deep research generation failed request=%s", record.get("request_id"))
        await thread.send(
            "심층 리서치 생성에 실패했습니다. 운영 로그를 확인해 주세요.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return
    await thread.send(
        _trim_discord(research, bot.config.max_response_chars),
        allowed_mentions=discord.AllowedMentions.none(),
    )


def traveler_forum_thread_title(record: dict[str, Any]) -> str:
    topic = str(record.get("topic") or "심층 출처 리서치 요청").strip()
    title = f"🧭 {topic}"
    return title[:90]


async def publish_traveler_forum_record(
    bot: JiphyeonjeonTravelerBot,
    record: dict[str, Any],
    ack: str,
) -> discord.Thread | None:
    channel = bot.get_channel(bot.config.traveler_channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(bot.config.traveler_channel_id)
        except discord.DiscordException:
            LOG.exception("failed to fetch traveler channel channel=%s", bot.config.traveler_channel_id)
            return None
    if not isinstance(channel, discord.ForumChannel):
        LOG.warning("traveler channel is not a forum channel=%s type=%s", bot.config.traveler_channel_id, type(channel).__name__)
        return None
    try:
        created = await channel.create_thread(
            name=traveler_forum_thread_title(record),
            content=ack,
            allowed_mentions=discord.AllowedMentions.none(),
            reason="Jiphyeonjeon Traveler deep research request",
        )
    except discord.DiscordException:
        LOG.exception(
            "failed to create traveler forum post channel=%s request=%s",
            bot.config.traveler_channel_id,
            record.get("request_id"),
        )
        return None
    return created.thread


def build_traveler_bot(config: TravelerBotConfig) -> JiphyeonjeonTravelerBot:
    bot = JiphyeonjeonTravelerBot(config)
    bot.tree.command(
        name="jiphyeonjeon_travel",
        description="Request deep research for durable high-trust collection sources",
    )(_travel_command)
    return bot


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_traveler_config()
    bot = build_traveler_bot(config)
    await bot.start(config.discord_bot_token)


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
