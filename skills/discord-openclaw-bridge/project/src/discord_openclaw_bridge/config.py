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


def load_config() -> BridgeConfig:
    _load_dotenv(Path.cwd() / ".env")

    discord_bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    if not discord_bot_token:
        raise ConfigError("missing required env var: DISCORD_BOT_TOKEN")

    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip()
    token_file = os.environ.get("OPENCLAW_GATEWAY_TOKEN_FILE", "").strip()
    if not token and token_file:
        path = Path(token_file).expanduser()
        if path.exists():
            token = path.read_text().strip()
    if not token:
        raise ConfigError("missing OPENCLAW_GATEWAY_TOKEN or readable OPENCLAW_GATEWAY_TOKEN_FILE")

    base_url = os.environ.get("OPENCLAW_BASE_URL", "http://127.0.0.1:18789/v1").strip().rstrip("/")
    if not (base_url.startswith("http://127.0.0.1:") or base_url.startswith("http://localhost:")):
        raise ConfigError("OPENCLAW_BASE_URL must remain loopback for this bridge")

    return BridgeConfig(
        discord_bot_token=discord_bot_token,
        guild_id=_required_int("DISCORD_GUILD_ID"),
        allowed_channel_id=_required_int("DISCORD_ALLOWED_CHANNEL_ID"),
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
    )
