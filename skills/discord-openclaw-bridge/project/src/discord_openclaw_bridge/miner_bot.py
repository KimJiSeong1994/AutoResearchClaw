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


def build_miner_bot(config: MinerBotConfig) -> JiphyeonjeonMinerBot:
    bot = JiphyeonjeonMinerBot(config)
    bot.tree.command(name="jiphyeonjeon_mine", description="Collect a link for Jiphyeonjeon-Claw review")(
        _mine_command
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
