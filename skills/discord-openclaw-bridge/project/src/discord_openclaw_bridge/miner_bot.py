from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import discord
from discord import app_commands

from .config import ConfigError, MinerBotConfig, load_miner_config
from .miner import DiscordLinkMetadata, record_message_links, record_requested_links, render_ack
from .traveler import TravelerResearchRequest, record_research_request, render_research_request_ack

LOG = logging.getLogger("discord_jiphyeonjeon_miner")


class JiphyeonjeonMinerBot(discord.Client):
    """Standalone Discord bot for 집현전-광부 link intake.

    The bot only collects links and writes pending-review records. It does not
    call OpenClaw, approve content, or publish newsletter/archive entries.
    """

    def __init__(self, config: MinerBotConfig):
        intents = discord.Intents.default()
        intents.message_content = config.miner_enable_channel_collection
        super().__init__(intents=intents)
        self.config = config
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        LOG.info("synced %s miner commands for guild=%s", len(synced), self.config.guild_id)

    async def on_ready(self) -> None:
        LOG.info("ready user=%s guild=%s miner_channel=%s", self.user, self.config.guild_id, self.config.miner_channel_id)

    async def on_message(self, message: discord.Message) -> None:
        if not self.config.miner_enable_channel_collection:
            return
        if message.author.bot or message.guild is None:
            return
        if message.guild.id != self.config.guild_id or message.channel.id != self.config.miner_channel_id:
            return
        try:
            results = record_message_links(
                message_text=message.content,
                intake_path=self.config.miner_intake_path,
                review_queue_path=self.config.miner_review_queue_path,
                discord=DiscordLinkMetadata(
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    message_id=message.id,
                    user_id=message.author.id,
                ),
            )
        except Exception:
            LOG.exception(
                "miner channel collection failed guild=%s channel=%s user=%s",
                message.guild.id,
                message.channel.id,
                message.author.id,
            )
            await message.reply("집현전-광부 링크 수집에 실패했습니다. 운영 로그를 확인해 주세요.", mention_author=False)
            return
        if results:
            await message.reply(render_ack(results), mention_author=False)

    def channel_allowed(self, interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild is not None
            and interaction.guild.id == self.config.guild_id
            and interaction.channel is not None
            and interaction.channel.id == self.config.miner_channel_id
        )

    def traveler_channel_allowed(self, interaction: discord.Interaction) -> bool:
        channel = interaction.channel
        channel_id = getattr(channel, "id", None)
        parent_id = getattr(channel, "parent_id", None)
        parent = getattr(channel, "parent", None)
        if parent_id is None and parent is not None:
            parent_id = getattr(parent, "id", None)
        return bool(
            self.config.traveler_channel_id is not None
            and interaction.guild is not None
            and interaction.guild.id == self.config.guild_id
            and channel is not None
            and (channel_id == self.config.traveler_channel_id or parent_id == self.config.traveler_channel_id)
        )


async def _mine_command(
    interaction: discord.Interaction,
    url: str,
    title: str | None = None,
    note: str | None = None,
) -> None:
    bot = interaction.client
    assert isinstance(bot, JiphyeonjeonMinerBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("집현전-광부 링크 수집은 지정된 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return
    try:
        results = record_requested_links(
            url=url,
            title=title,
            note=note,
            intake_path=bot.config.miner_intake_path,
            review_queue_path=bot.config.miner_review_queue_path,
            discord=DiscordLinkMetadata(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id if interaction.user else None,
            ),
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    except Exception:
        LOG.exception("miner slash request failed guild=%s channel=%s", interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message("집현전-광부 링크 수집에 실패했습니다. 운영 로그를 확인해 주세요.", ephemeral=True)
        return
    await interaction.response.send_message(render_ack(results), ephemeral=True)


async def _travel_command(
    interaction: discord.Interaction,
    topic: str,
    scope: str | None = None,
    min_sources_to_review: int = 20,
    note: str | None = None,
) -> None:
    bot = interaction.client
    assert isinstance(bot, JiphyeonjeonMinerBot)
    if not bot.traveler_channel_allowed(interaction):
        await interaction.response.send_message("집현전-여행자 리서치 요청은 지정된 여행자 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return
    if bot.config.traveler_research_queue_path is None or bot.config.traveler_source_queue_path is None:
        await interaction.response.send_message("집현전-여행자 큐 경로가 설정되지 않았습니다.", ephemeral=True)
        return
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
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    except Exception:
        LOG.exception("traveler slash request failed guild=%s channel=%s", interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message("집현전-여행자 리서치 요청 등록에 실패했습니다. 운영 로그를 확인해 주세요.", ephemeral=True)
        return
    ack = render_research_request_ack(record)
    forum_thread = await _publish_traveler_forum_record(bot, record, ack)
    suffix = f"\n포럼 게시글: {forum_thread.mention}" if forum_thread is not None else ""
    await interaction.response.send_message(f"{ack}{suffix}", ephemeral=True)


def _traveler_forum_thread_title(record: dict[str, Any]) -> str:
    topic = str(record.get("topic") or "심층 출처 리서치 요청").strip()
    title = f"🧭 {topic}"
    return title[:90]


async def _publish_traveler_forum_record(
    bot: JiphyeonjeonMinerBot,
    record: dict[str, Any],
    ack: str,
) -> discord.Thread | None:
    channel_id = bot.config.traveler_channel_id
    if channel_id is None:
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException:
            LOG.exception("failed to fetch traveler channel channel=%s", channel_id)
            return None
    if not isinstance(channel, discord.ForumChannel):
        return None
    try:
        created = await channel.create_thread(
            name=_traveler_forum_thread_title(record),
            content=ack,
            allowed_mentions=discord.AllowedMentions.none(),
            reason="Jiphyeonjeon Traveler deep research request",
        )
    except discord.DiscordException:
        LOG.exception("failed to create traveler forum post channel=%s request=%s", channel_id, record.get("request_id"))
        return None
    return created.thread


def build_miner_bot(config: MinerBotConfig) -> JiphyeonjeonMinerBot:
    bot = JiphyeonjeonMinerBot(config)
    bot.tree.command(name="jiphyeonjeon_mine", description="Collect a link for Jiphyeonjeon-Claw review")(
        _mine_command
    )
    bot.tree.command(name="jiphyeonjeon_travel", description="Request deep research for durable high-trust collection sources")(
        _travel_command
    )
    return bot


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_miner_config()
    bot = build_miner_bot(config)
    await bot.start(config.discord_bot_token)


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
