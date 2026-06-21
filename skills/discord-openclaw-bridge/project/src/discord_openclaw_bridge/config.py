from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when required runtime configuration is missing or unsafe."""


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _required_int(name: str) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"missing required env var: {name}")
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a Discord snowflake integer") from exc


def _optional_int(name: str, default: int | None = None) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a Discord snowflake integer") from exc


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BridgeConfig:
    discord_bot_token: str
    guild_id: int
    allowed_channel_id: int
    openclaw_base_url: str
    openclaw_gateway_token: str
    openclaw_model: str
    timeout_sec: float
    enable_mention_responses: bool
    max_prompt_chars: int
    max_response_chars: int
    briefing_source_path: Path
    miner_channel_id: int
    reporter_channel_id: int | None
    miner_intake_path: Path
    miner_review_queue_path: Path
    miner_enable_channel_collection: bool


@dataclass(frozen=True)
class MinerBotConfig:
    discord_bot_token: str
    guild_id: int
    miner_channel_id: int
    miner_intake_path: Path
    miner_review_queue_path: Path
    miner_enable_channel_collection: bool
    traveler_client_id: int | None = None
    traveler_channel_id: int | None = None
    traveler_research_queue_path: Path | None = None
    traveler_source_queue_path: Path | None = None


@dataclass(frozen=True)
class TravelerBotConfig:
    discord_bot_token: str
    guild_id: int
    traveler_channel_id: int
    traveler_research_queue_path: Path
    traveler_source_queue_path: Path
    openclaw_base_url: str = "http://127.0.0.1:18789/v1"
    openclaw_gateway_token: str = ""
    openclaw_model: str = "openclaw/clawbridge"
    timeout_sec: float = 120.0
    max_response_chars: int = 1800


@dataclass(frozen=True)
class ReporterBotConfig:
    discord_bot_token: str
    guild_id: int
    reporter_channel_id: int
    reporter_draft_dir: Path
    max_response_chars: int = 1800


def _miner_intake_path() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_MINER_INTAKE_PATH",
            str(Path.home() / ".openclaw" / "workspace" / "intake" / "jiphyeonjeon-miner" / "links.jsonl"),
        )
    ).expanduser()


def _miner_review_queue_path() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_MINER_REVIEW_QUEUE_PATH",
            str(
                Path.home()
                / ".openclaw"
                / "workspace"
                / "review"
                / "jiphyeonjeon-claw"
                / "link-review-queue.jsonl"
            ),
        )
    ).expanduser()


def _miner_channel_id(default: int | None = None) -> int:
    channel_id = _optional_int("DISCORD_MINER_CHANNEL_ID", default)
    if channel_id is None:
        raise ConfigError("missing required env var: DISCORD_MINER_CHANNEL_ID")
    return channel_id


def _traveler_channel_id(default: int | None = None) -> int | None:
    return _optional_int("DISCORD_TRAVELER_CHANNEL_ID", default)


def _traveler_client_id(default: int | None = None) -> int | None:
    return _optional_int("DISCORD_TRAVELER_CLIENT_ID", default)


def _traveler_research_queue_path() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_TRAVELER_RESEARCH_QUEUE_PATH",
            str(Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-traveler" / "research-requests.jsonl"),
        )
    ).expanduser()


def _traveler_source_queue_path() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_TRAVELER_SOURCE_QUEUE_PATH",
            str(Path.home() / ".openclaw" / "workspace" / "review" / "jiphyeonjeon-traveler" / "source-candidates.jsonl"),
        )
    ).expanduser()


def _reporter_channel_id(default: int | None = None) -> int | None:
    return _optional_int("DISCORD_REPORTER_CHANNEL_ID", default)


def _reporter_draft_dir() -> Path:
    return Path(
        os.environ.get(
            "JIPHYEONJEON_REPORTER_DRAFT_DIR",
            str(Path.home() / ".openclaw" / "workspace" / "blog-drafts"),
        )
    ).expanduser()


def _env_value(name: str) -> str:
    return os.environ.get(name, "").strip()


def _agent_gateway_token() -> str:
    token = _env_value("HERMES_GATEWAY_TOKEN")
    if token:
        return token
    token = _env_value("OPENCLAW_GATEWAY_TOKEN")
    if token:
        return token

    for name in ("HERMES_GATEWAY_TOKEN_FILE", "OPENCLAW_GATEWAY_TOKEN_FILE"):
        token_file = _env_value(name)
        if not token_file:
            continue
        path = Path(token_file).expanduser()
        if path.exists():
            return path.read_text().strip()
    return ""


def _openclaw_gateway_token() -> str:
    return _agent_gateway_token()


def _agent_base_url() -> tuple[str, str]:
    hermes_base_url = _env_value("HERMES_BASE_URL")
    if hermes_base_url:
        return hermes_base_url.rstrip("/"), "HERMES_BASE_URL"
    openclaw_base_url = _env_value("OPENCLAW_BASE_URL") or "http://127.0.0.1:18789/v1"
    return openclaw_base_url.rstrip("/"), "OPENCLAW_BASE_URL"


def _openclaw_base_url() -> str:
    base_url, source_name = _agent_base_url()
    if not (base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")):
        raise ConfigError(f"{source_name} must remain loopback for this bridge")
    return base_url


def _agent_model() -> str:
    return _env_value("HERMES_MODEL") or _env_value("OPENCLAW_MODEL") or "openclaw/clawbridge"


def _agent_timeout_sec() -> float:
    return float(_env_value("HERMES_TIMEOUT_SEC") or _env_value("OPENCLAW_TIMEOUT_SEC") or "120")


def load_config() -> BridgeConfig:
    _load_dotenv(Path.cwd() / ".env")

    discord_bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not discord_bot_token:
        raise ConfigError("missing required env var: DISCORD_BOT_TOKEN")

    token = _openclaw_gateway_token()
    if not token:
        raise ConfigError("missing HERMES_GATEWAY_TOKEN/OPENCLAW_GATEWAY_TOKEN or readable gateway token file")

    base_url = _openclaw_base_url()

    allowed_channel_id = _required_int("DISCORD_ALLOWED_CHANNEL_ID")
    miner_channel_id = _miner_channel_id(default=allowed_channel_id)

    return BridgeConfig(
        discord_bot_token=discord_bot_token,
        guild_id=_required_int("DISCORD_GUILD_ID"),
        allowed_channel_id=allowed_channel_id,
        openclaw_base_url=base_url,
        openclaw_gateway_token=token,
        openclaw_model=_agent_model(),
        timeout_sec=_agent_timeout_sec(),
        enable_mention_responses=_bool_env("DISCORD_ENABLE_MENTION_RESPONSES", False),
        max_prompt_chars=int(os.environ.get("DISCORD_MAX_PROMPT_CHARS", "4000")),
        max_response_chars=int(os.environ.get("DISCORD_MAX_RESPONSE_CHARS", "1800")),
        briefing_source_path=Path(
            os.environ.get(
                "DISCORD_BRIEFING_SOURCE",
                str(Path.home() / ".openclaw" / "workspace" / "reports" / "daily-trends-latest.md"),
            )
        ).expanduser(),
        miner_channel_id=miner_channel_id,
        reporter_channel_id=_optional_int("DISCORD_REPORTER_CHANNEL_ID", None),
        miner_intake_path=_miner_intake_path(),
        miner_review_queue_path=_miner_review_queue_path(),
        miner_enable_channel_collection=_bool_env("DISCORD_MINER_ENABLE_CHANNEL_COLLECTION", False),
    )


def load_miner_config() -> MinerBotConfig:
    _load_dotenv(Path.cwd() / ".env")

    discord_bot_token = os.environ.get("DISCORD_MINER_BOT_TOKEN", "").strip()
    if not discord_bot_token:
        raise ConfigError("missing required env var: DISCORD_MINER_BOT_TOKEN")

    default_channel_id = _optional_int("DISCORD_ALLOWED_CHANNEL_ID")
    miner_channel_id = _miner_channel_id(default=default_channel_id)
    return MinerBotConfig(
        discord_bot_token=discord_bot_token,
        guild_id=_required_int("DISCORD_GUILD_ID"),
        miner_channel_id=miner_channel_id,
        miner_intake_path=_miner_intake_path(),
        miner_review_queue_path=_miner_review_queue_path(),
        miner_enable_channel_collection=_bool_env("DISCORD_MINER_ENABLE_CHANNEL_COLLECTION", False),
        traveler_client_id=_traveler_client_id(default=None),
        traveler_channel_id=_traveler_channel_id(default=None),
        traveler_research_queue_path=_traveler_research_queue_path(),
        traveler_source_queue_path=_traveler_source_queue_path(),
    )


def load_traveler_config() -> TravelerBotConfig:
    _load_dotenv(Path.cwd() / ".env")

    discord_bot_token = os.environ.get("DISCORD_TRAVELER_BOT_TOKEN", "").strip()
    if not discord_bot_token:
        raise ConfigError("missing required env var: DISCORD_TRAVELER_BOT_TOKEN")

    traveler_channel_id = _traveler_channel_id(default=None)
    if traveler_channel_id is None:
        raise ConfigError("missing required env var: DISCORD_TRAVELER_CHANNEL_ID")

    return TravelerBotConfig(
        discord_bot_token=discord_bot_token,
        guild_id=_required_int("DISCORD_GUILD_ID"),
        traveler_channel_id=traveler_channel_id,
        traveler_research_queue_path=_traveler_research_queue_path(),
        traveler_source_queue_path=_traveler_source_queue_path(),
        openclaw_base_url=_openclaw_base_url(),
        openclaw_gateway_token=_openclaw_gateway_token(),
        openclaw_model=_agent_model(),
        timeout_sec=_agent_timeout_sec(),
        max_response_chars=int(os.environ.get("DISCORD_MAX_RESPONSE_CHARS", "1800")),
    )

def load_reporter_config() -> ReporterBotConfig:
    _load_dotenv(Path.cwd() / ".env")

    discord_bot_token = os.environ.get("DISCORD_REPORTER_BOT_TOKEN", "").strip()
    if not discord_bot_token:
        raise ConfigError("missing required env var: DISCORD_REPORTER_BOT_TOKEN")

    default_channel_id = _optional_int("DISCORD_ALLOWED_CHANNEL_ID")
    reporter_channel_id = _reporter_channel_id(default=default_channel_id)
    if reporter_channel_id is None:
        raise ConfigError("missing required env var: DISCORD_REPORTER_CHANNEL_ID")

    return ReporterBotConfig(
        discord_bot_token=discord_bot_token,
        guild_id=_required_int("DISCORD_GUILD_ID"),
        reporter_channel_id=reporter_channel_id,
        reporter_draft_dir=_reporter_draft_dir(),
        max_response_chars=int(os.environ.get("DISCORD_MAX_RESPONSE_CHARS", "1800")),
    )

