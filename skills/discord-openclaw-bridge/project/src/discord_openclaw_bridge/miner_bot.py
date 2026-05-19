from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord import app_commands

from .config import ConfigError, MinerBotConfig, load_miner_config
from .miner import DiscordLinkMetadata, record_message_links, record_requested_links, render_ack

LOG = logging.getLogger("discord_jiphyeonjeon_miner")


class JiphyeonjeonMinerBot(discord.Client):
    """Standalone Discord bot for 집현전-광부 link intake.

    The bot only collects links and writes pending-review records. It does not
    call OpenClaw, approve content, or publish newsletter/archive entries.
    """

    def __init__(self, config: MinerBotConfig):
        intents = discord.Intents.default()
        intents.message_content = config.miner_enable_channel_collection or config.traveler_client_id is not None
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

    def traveler_relay_allowed(self, message: discord.Message) -> bool:
        if self.config.traveler_client_id is None or self.user is None:
            return False
        author_id = getattr(message.author, "id", None)
        mentions = getattr(message, "mentions", []) or []
        mentioned_ids = {getattr(user, "id", None) for user in mentions}
        return bool(
            getattr(message.author, "bot", False)
            and author_id == self.config.traveler_client_id
            and self.user.id in mentioned_ids
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if message.guild.id != self.config.guild_id or message.channel.id != self.config.miner_channel_id:
            return
        traveler_relay = self.traveler_relay_allowed(message)
        if not self.config.miner_enable_channel_collection and not traveler_relay:
            return
        if message.author.bot and not traveler_relay:
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



async def _mine_youtube_channel_command(
    interaction: discord.Interaction,
    channel_url: str,
    max_videos: int = 5,
    note: str | None = None,
) -> None:
    bot = interaction.client
    assert isinstance(bot, JiphyeonjeonMinerBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("집현전-광부 YouTube 채널 수집은 지정된 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return
    safe_max_videos = max(1, min(25, int(max_videos or 5)))
    try:
        results = record_requested_links(
            url=channel_url,
            note=note,
            intake_path=bot.config.miner_intake_path,
            review_queue_path=bot.config.miner_review_queue_path,
            discord=DiscordLinkMetadata(
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                user_id=interaction.user.id if interaction.user else None,
            ),
            channel_max_videos=safe_max_videos,
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return
    except Exception:
        LOG.exception("miner YouTube channel request failed guild=%s channel=%s", interaction.guild_id, interaction.channel_id)
        await interaction.response.send_message("집현전-광부 YouTube 채널 수집에 실패했습니다. 운영 로그를 확인해 주세요.", ephemeral=True)
        return
    await interaction.response.send_message(render_ack(results), ephemeral=True)

def build_miner_bot(config: MinerBotConfig) -> JiphyeonjeonMinerBot:
    bot = JiphyeonjeonMinerBot(config)
    bot.tree.command(name="jiphyeonjeon_mine", description="Collect a link for Jiphyeonjeon-Claw review")(
        _mine_command
    )
    bot.tree.command(
        name="jiphyeonjeon_mine_yt_channel",
        description="Collect recent YouTube channel videos for Jiphyeonjeon-Claw review",
    )(_mine_youtube_channel_command)
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
