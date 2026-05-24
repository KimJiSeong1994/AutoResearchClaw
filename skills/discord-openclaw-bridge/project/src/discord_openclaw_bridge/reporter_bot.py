from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import discord
from discord import app_commands

from .blog_publisher import BlogPublisherError, build_payload, load_draft, validate_public_payload
from .config import ConfigError, ReporterBotConfig, load_reporter_config

LOG = logging.getLogger("discord_jiphyeonjeon_reporter")


class JiphyeonjeonReporterBot(discord.Client):
    """Standalone Discord app/bot for 집현전-기자 newsroom blog operations.

    The bot exposes app commands in the newsroom forum and its threads. It can
    preview evidence-gated blog drafts, but it does not publish posts to the
    public site; publication remains behind the blog publisher's approval gate.
    """

    def __init__(self, config: ReporterBotConfig):
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.config.guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        LOG.info("synced %s reporter commands for guild=%s", len(synced), self.config.guild_id)

    async def on_ready(self) -> None:
        LOG.info(
            "ready user=%s guild=%s reporter_channel=%s",
            self.user,
            self.config.guild_id,
            self.config.reporter_channel_id,
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
            and (channel_id == self.config.reporter_channel_id or parent_id == self.config.reporter_channel_id)
        )


def _trim_discord(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 20)].rstrip() + "\n…(truncated)"


def render_reporter_status(config: ReporterBotConfig) -> str:
    return (
        "📰 **집현전-기자 앱 활성화**\n"
        f"- 뉴스룸 채널: <#{config.reporter_channel_id}>\n"
        "- 역할: 수집·검토된 기술 아티클을 근거 추적 가능한 블로그 초안으로 재구성\n"
        "- 안전장치: 공개 출처 링크, evidence table, claim layering, dry-run 검증 후 운영자 승인 게시\n"
        "- 게시 경계: 이 Discord 앱은 초안/미리보기만 수행하며, 공개 블로그 게시에는 별도 approval-id가 필요합니다."
    )


def _resolve_draft_path(config: ReporterBotConfig, source: str | None) -> Path:
    if source:
        candidate = Path(source).expanduser()
        if not candidate.is_absolute():
            candidate = (config.reporter_draft_dir / candidate).resolve()
        return candidate
    matches = sorted(config.reporter_draft_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not matches:
        raise BlogPublisherError(f"no markdown draft found in {config.reporter_draft_dir}")
    return matches[0]


def render_draft_preview(config: ReporterBotConfig, source: str | None = None) -> str:
    draft_path = _resolve_draft_path(config, source)
    payload = build_payload(load_draft(draft_path))
    validate_public_payload(payload)
    source_count = payload["content"].count("https://") + payload["content"].count("http://")
    return (
        "📝 **집현전-기자 초안 미리보기**\n"
        f"- 파일: `{draft_path.name}`\n"
        f"- 제목: {payload['title']}\n"
        f"- 슬러그: `{payload['slug']}`\n"
        f"- 태그: {', '.join(payload['tags'])}\n"
        f"- 예상 읽기 시간: {payload['reading_time_min']}분\n"
        f"- 공개 URL 근거 수: {source_count}\n"
        f"- 요약: {payload['excerpt']}\n"
        "\n공개 게시 전 `discord-openclaw-post-blog --dry-run` 및 approval-id 검증을 별도로 통과해야 합니다."
    )


async def _reporter_status_command(interaction: discord.Interaction) -> None:
    bot = interaction.client
    assert isinstance(bot, JiphyeonjeonReporterBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("집현전-기자 앱은 지정된 뉴스룸 포럼에서만 사용할 수 있습니다.", ephemeral=True)
        return
    await interaction.response.send_message(render_reporter_status(bot.config), ephemeral=True)


async def _reporter_preview_command(interaction: discord.Interaction, source: str | None = None) -> None:
    bot = interaction.client
    assert isinstance(bot, JiphyeonjeonReporterBot)
    if not bot.channel_allowed(interaction):
        await interaction.response.send_message("집현전-기자 초안 미리보기는 지정된 뉴스룸 포럼에서만 사용할 수 있습니다.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        preview = await asyncio.to_thread(render_draft_preview, bot.config, source)
    except Exception as exc:
        LOG.exception("reporter preview failed guild=%s channel=%s", interaction.guild_id, interaction.channel_id)
        await interaction.followup.send(f"집현전-기자 초안 미리보기에 실패했습니다: {exc}", ephemeral=True)
        return
    await interaction.followup.send(_trim_discord(preview, bot.config.max_response_chars), ephemeral=True)


def build_reporter_bot(config: ReporterBotConfig) -> JiphyeonjeonReporterBot:
    bot = JiphyeonjeonReporterBot(config)
    bot.tree.command(
        name="jiphyeonjeon_reporter_status",
        description="Show the Jiphyeonjeon Reporter app status and publication safety boundary",
    )(_reporter_status_command)
    bot.tree.command(
        name="jiphyeonjeon_reporter_preview",
        description="Preview an evidence-gated Jiphyeonjeon blog draft without publishing",
    )(app_commands.describe(source="Optional draft filename under the reporter draft directory")(_reporter_preview_command))
    return bot


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_reporter_config()
    bot = build_reporter_bot(config)
    await bot.start(config.discord_bot_token)


def main() -> None:
    try:
        asyncio.run(run())
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
