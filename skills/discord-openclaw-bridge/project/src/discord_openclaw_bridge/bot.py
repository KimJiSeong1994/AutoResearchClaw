from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import discord
from discord import app_commands

from .briefing import render_briefing
from .config import BridgeConfig, ConfigError, load_config
from .openclaw import OpenClawClient

LOG = logging.getLogger("discord_openclaw_bridge")
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
JIPHYEONJEON_AGENT_IMAGE_FILES = (
    ASSETS_DIR / "jiphyeonjeon-editor-agent.png",
    ASSETS_DIR / "jiphyeonjeon-advisor-agent.png",
)



def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n…(truncated)"


class OpenClawDiscordBot(discord.Client):
    def __init__(self, config: BridgeConfig):
        intents = discord.Intents.default()
        intents.message_content = config.enable_mention_responses
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
        if message.channel.id == self.config.miner_channel_id:
            return

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

def jiphyeonjeon_agent_image_paths() -> list[Path]:
    """Return available visual identity assets for the Jiphyeonjeon registry."""

    return [path for path in JIPHYEONJEON_AGENT_IMAGE_FILES if path.exists()]


def render_jiphyeonjeon_agent_registry() -> str:
    """Return a Discord-safe roster/workflow summary for Jiphyeonjeon agents.

    This is a registration/help surface only. It does not execute the advisory
    scripts, mutate queues, approve content, or trigger publishing.
    """

    return (
        "**집현전 에이전트 등록 현황**\n"
        "- 집현전-여행자: 공개 출처 후보를 찾는 research-only agent. 광부 seed/클로 review로 넘기지만 직접 승인하지 않습니다.\n"
        "- 집현전-광부: Discord/seed 링크를 수집해 pending review queue에 넣는 collection-only agent.\n"
        "- 집현전-클로: Miner pending link를 approve/reject/hold로 판단하는 content review owner.\n"
        "- 집현정-편집자: 여러 표면의 JSON/JSONL artifact를 canonical identity로 묶고 중복 그룹을 보고하는 advisory-only agent. 이미지: jiphyeonjeon-editor-agent.png\n"
        "- 집현전-지도교수: 연구/게시 artifact의 evidence URL, source diversity, citation coverage, overclaim risk를 검토하는 advisory-only agent. 이미지: jiphyeonjeon-advisor-agent.png\n"
        "- 집현전-경비원: stale run, backlog, handoff 실패를 관측하는 ops guard.\n"
        "- Card-news publisher: sanitized archive를 Discord card-news로 렌더링하되 quality gate 실패 시 게시 전 중단합니다.\n\n"
        "**권장 워크프로세스**\n"
        "1. 여행자 → 후보 출처 발굴 및 evidence-backed source candidate 기록.\n"
        "2. 광부 → 링크 수집, sanitize, pending_claw_review queue 기록.\n"
        "3. 클로 → approve/reject/hold append-only decision 및 approved-only export.\n"
        "4. 집현정-편집자 → newsletter/manual/card/wiki/research artifact 간 중복·동일성 advisory report.\n"
        "5. 집현전-지도교수 → 공개 근거·인용 품질 advisory verdict.\n"
        "6. 사람 편집 검토 → promotion coordinator는 아직 pending_future_phase. 자동 승격 없음.\n"
        "7. Publisher → 승인된 sanitized artifact만 게시. 게시/삭제 전 quality gate와 운영 설정 확인.\n\n"
        "**안전 경계**: 이 명령은 등록/안내 전용입니다. queue 수정, 승인, promotion, Discord 게시를 실행하지 않습니다."
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


async def _agents_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, OpenClawDiscordBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("집현전 에이전트 등록 현황은 지정된 채널에서만 확인할 수 있습니다.", ephemeral=True)
        return
    image_paths = jiphyeonjeon_agent_image_paths()
    files = [discord.File(path, filename=path.name) for path in image_paths]
    await interaction.response.send_message(
        _trim(render_jiphyeonjeon_agent_registry(), bot.config.max_response_chars),
        files=files,
        ephemeral=False,
    )


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


def build_bot(config: BridgeConfig) -> OpenClawDiscordBot:
    bot = OpenClawDiscordBot(config)
    bot.tree.command(name="openclaw", description="Ask OpenClaw from the allowlisted channel")(_openclaw_command)
    bot.tree.command(name="jiphyeonjeon_briefing", description="Post the latest Jiphyeonjeon-Claw AI briefing")(_briefing_command)
    bot.tree.command(name="jiphyeonjeon_agents", description="Show the registered Jiphyeonjeon agent roster and workflow")(_agents_command)
    bot.tree.command(name="openclaw_status", description="Check the loopback OpenClaw gateway")(_status_command)
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
