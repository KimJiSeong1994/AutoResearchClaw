from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from discord_openclaw_bridge.post_newsletter import (  # noqa: E402
    NewsletterPostConfigError,
    _load_message,
    _required_snowflake,
)


def test_newsletter_message_loader_truncates() -> None:
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "briefing.md"
        path.write_text("a" * 50, encoding="utf-8")

        body = _load_message(path, max_chars=20)

    assert body.endswith("…(briefing truncated)")


def test_newsletter_channel_requires_snowflake(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_NEWSLETTER_CHANNEL_ID", "not-a-number")

    import pytest

    with pytest.raises(NewsletterPostConfigError, match="snowflake"):
        _required_snowflake("DISCORD_NEWSLETTER_CHANNEL_ID")
