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


def _openclaw_gateway_token() -> str:
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    token_file = os.environ.get("OPENCLAW_GATEWAY_TOKEN_FILE", "").strip()
    if not token and token_file:
        path = Path(token_file).expanduser()
        if path.exists():
            token = path.read_text().strip()
    return token


def _openclaw_base_url() -> str:
    base_url = os.environ.get("OPENCLAW_BASE_URL", "http://127.0.0.1:18789/v1").strip().rstrip("/")
    if not (base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")):
        raise ConfigError("OPENCLAW_BASE_URL must remain loopback for this bridge")
    return base_url


def load_config() -> BridgeConfig:
    _load_dotenv(Path.cwd() / ".env")

    discord_bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not discord_bot_token:
        raise ConfigError("missing required env var: DISCORD_BOT_TOKEN")

    token = _openclaw_gateway_token()
    if not token:
        raise ConfigError("missing OPENCLAW_GATEWAY_TOKEN or readable OPENCLAW_GATEWAY_TOKEN_FILE")

    base_url = _openclaw_base_url()

    allowed_channel_id = _required_int("DISCORD_ALLOWED_CHANNEL_ID")
    miner_channel_id = _miner_channel_id(default=allowed_channel_id)

    return BridgeConfig(
        discord_bot_token=discord_bot_token,
        guild_id=_required_int("DISCORD_GUILD_ID"),
        allowed_channel_id=allowed_channel_id,
        openclaw_base_url=base_url,
        openclaw_gateway_token=token,
        openclaw_model=os.environ.get("OPENCLAW_MODEL", "openclaw/clawbridge").strip(),
        timeout_sec=float(os.environ.get("OPENCLAW_TIMEOUT_SEC", "120")),
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
        openclaw_model=os.environ.get("OPENCLAW_MODEL", "openclaw/clawbridge").strip(),
        timeout_sec=float(os.environ.get("OPENCLAW_TIMEOUT_SEC", "120")),
        max_response_chars=int(os.environ.get("DISCORD_MAX_RESPONSE_CHARS", "1800")),
    )
