from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord import app_commands

from .briefing import render_briefing
from .config import BridgeConfig, ConfigError, load_config
from .miner import DiscordLinkMetadata, record_message_links, record_miner_link, render_ack
from .openclaw import OpenClawClient

LOG = logging.getLogger("discord_openclaw_bridge")


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n…(truncated)"


class OpenClawDiscordBot(discord.Client):
    def __init__(self, config: BridgeConfig):
        intents = discord.Intents.default()
        intents.message_content = config.enable_mention_responses or config.miner_enable_channel_collection
        super().__init__(intents=intents)
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
        LOG.info("synced %s commands for guild=%s", len(synced), self.config.guild_id)

    async def on_ready(self) -> None:
        LOG.info(
            "ready user=%s guild=%s channel=%s miner_channel=%s",
            self.user,
            self.config.guild_id,
            self.config.allowed_channel_id,
            self.config.miner_channel_id,
        )

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.guild.id != self.config.guild_id:
            return

        if self.config.miner_enable_channel_collection and message.channel.id == self.config.miner_channel_id:
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

        if not self.config.enable_mention_responses:
            return
        if message.channel.id != self.config.allowed_channel_id:
            return
        if self.user is None or self.user not in message.mentions:
            return
        prompt = message.content.replace(self.user.mention, "", 1).strip()
        if not prompt:
            await message.reply("무엇을 도와드릴까요? `/openclaw` 명령도 사용할 수 있습니다.", mention_author=False)
            return
        prompt = _trim(prompt, self.config.max_prompt_chars)
        async with message.channel.typing():
            try:
                answer = await self.openclaw.chat(prompt)
            except Exception:
                LOG.exception("mention request failed guild=%s channel=%s user=%s", message.guild.id, message.channel.id, message.author.id)
                await message.reply("OpenClaw 호출에 실패했습니다. 운영 로그를 확인해 주세요.", mention_author=False)
                return
        await message.reply(_trim(answer, self.config.max_response_chars), mention_author=False)

    def channel_allowed(self, interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild is not None
            and interaction.guild.id == self.config.guild_id
            and interaction.channel is not None
            and interaction.channel.id == self.config.allowed_channel_id
        )

    def miner_channel_allowed(self, interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild is not None
            and interaction.guild.id == self.config.guild_id
            and interaction.channel is not None
            and interaction.channel.id == self.config.miner_channel_id
        )


async def _openclaw_command(interaction: discord.Interaction, prompt: str) -> None:
    bot = interaction.client
    assert isinstance(bot, OpenClawDiscordBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("이 OpenClaw 앱은 지정된 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return

    prompt = _trim(prompt.strip(), bot.config.max_prompt_chars)
    if not prompt:
        await interaction.response.send_message("prompt를 입력해 주세요.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True)
    try:
        answer = await bot.openclaw.chat(prompt)
    except Exception:
        LOG.exception(
            "slash request failed guild=%s channel=%s user=%s",
            interaction.guild_id,
            interaction.channel_id,
            interaction.user.id if interaction.user else None,
        )
        await interaction.followup.send("OpenClaw 호출에 실패했습니다. 운영 로그를 확인해 주세요.")
        return
    await interaction.followup.send(_trim(answer, bot.config.max_response_chars))


async def _briefing_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, OpenClawDiscordBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("이 OpenClaw 앱은 지정된 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    try:
        briefing = render_briefing(bot.config.briefing_source_path, max_chars=bot.config.max_response_chars)
    except FileNotFoundError:
        LOG.exception("briefing source missing path=%s", bot.config.briefing_source_path)
        await interaction.followup.send("브리핑 원본 리포트를 찾지 못했습니다. 운영 로그를 확인해 주세요.")
        return
    except Exception:
        LOG.exception("briefing render failed path=%s", bot.config.briefing_source_path)
        await interaction.followup.send("브리핑 생성에 실패했습니다. 운영 로그를 확인해 주세요.")
        return
    await interaction.followup.send(briefing.body)


async def _status_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, OpenClawDiscordBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("이 OpenClaw 앱은 지정된 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        status = await bot.openclaw.health()
    except Exception:
        LOG.exception("status request failed guild=%s channel=%s", interaction.guild_id, interaction.channel_id)
        await interaction.followup.send("OpenClaw gateway health: FAIL")
        return
    await interaction.followup.send(f"OpenClaw gateway health: {status}")


async def _mine_command(
    interaction: discord.Interaction,
    url: str,
    title: str | None = None,
    note: str | None = None,
) -> None:
    bot = interaction.client
    assert isinstance(bot, OpenClawDiscordBot)
    if not bot.miner_channel_allowed(interaction):
        await interaction.response.send_message("집현전-광부 링크 수집은 지정된 채널에서만 사용할 수 있습니다.", ephemeral=True)
        return
    try:
        result = record_miner_link(
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
    await interaction.response.send_message(render_ack([result]), ephemeral=True)


def build_bot(config: BridgeConfig) -> OpenClawDiscordBot:
    bot = OpenClawDiscordBot(config)
    bot.tree.command(name="openclaw", description="Ask OpenClaw from the allowlisted channel")(_openclaw_command)
    bot.tree.command(name="jiphyeonjeon_briefing", description="Post the latest Jiphyeonjeon-Claw AI briefing")(_briefing_command)
    bot.tree.command(name="openclaw_status", description="Check the loopback OpenClaw gateway")(_status_command)
    bot.tree.command(name="jiphyeonjeon_mine", description="Collect a link for Jiphyeonjeon-Claw review")(_mine_command)
    return bot


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    bot = build_bot(config)
    await bot.start(config.discord_bot_token)


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
