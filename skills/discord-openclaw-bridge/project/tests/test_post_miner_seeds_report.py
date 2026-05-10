"""Tests for discord_openclaw_bridge.post_miner_seeds_report.

Cover the pure formatters (`_format_thread_title`, `_format_thread_body`).
Discord HTTP I/O is exercised live in production; unit tests stay offline.
"""
from __future__ import annotations

import pytest

from discord_openclaw_bridge.post_miner_seeds_report import (
    AGENT_DISPLAY_NAME,
    AGENT_ID,
    DISCORD_THREAD_TITLE_LIMIT,
    ReportConfigError,
    _format_thread_body,
    _format_thread_title,
    _resolve_bot_token,
)


_HEALTHY = {
    "run_at": "2026-05-10T21:00:23Z",
    "duration_sec": 49.3,
    "seeds_total": 1,
    "seeds_processed": 1,
    "seeds_skipped_cooldown": 0,
    "seeds_with_errors": 0,
    "seeds_with_warnings": 0,
    "total_expanded": 10,
    "total_accepted": 10,
    "total_duplicate": 0,
    "total_rejected": 0,
    "intake_path": "/home/ubuntu/.openclaw/workspace/intake/jiphyeonjeon-miner/links.jsonl",
    "review_queue_path": "/home/ubuntu/.openclaw/workspace/review/jiphyeonjeon-claw/link-review-queue.jsonl",
    "summaries": [
        {
            "seed_url": "https://www.nature.com/nature/articles?type=article",
            "expanded_count": 10,
            "accepted": 10,
            "duplicate": 0,
            "rejected": 0,
            "skipped_cooldown": False,
            "error": None,
        }
    ],
}


def test_title_healthy_uses_kst_date_and_rock_emoji() -> None:
    title = _format_thread_title(_HEALTHY)
    assert "Miner Seeds 2026-05-11" in title  # 21:00 UTC = 06:00 KST next day
    assert "accepted=10" in title
    assert "errors=0" in title
    assert title.startswith("🪨")
    assert len(title) <= DISCORD_THREAD_TITLE_LIMIT


def test_title_with_errors_uses_alert_emoji() -> None:
    payload = {**_HEALTHY, "seeds_with_errors": 1, "total_accepted": 0}
    title = _format_thread_title(payload)
    assert title.startswith("🚨")
    assert "errors=1" in title


def test_title_distinguishes_transient_warning_from_real_error() -> None:
    """An empty_expansion warning must use ⚠️, not 🚨, to avoid alert fatigue.

    Regression for review fix H2: Nature selector drift / rate-limit responses
    surface as `seeds_with_warnings`. Without this branch every retry-able
    transient looked like a real outage, desensitising operators to genuine
    🚨 cases.
    """
    payload = {
        **_HEALTHY,
        "seeds_with_errors": 0,
        "seeds_with_warnings": 1,
        "total_accepted": 0,
    }
    title = _format_thread_title(payload)
    assert title.startswith("⚠️")
    assert "🚨" not in title
    assert "warnings=1" in title


def test_title_zero_accepted_no_errors_uses_warning_emoji() -> None:
    """Zero accepted with no cooldown skips signals an unexpected dry run."""
    payload = {
        **_HEALTHY,
        "total_accepted": 0,
        "seeds_with_errors": 0,
        "seeds_skipped_cooldown": 0,
    }
    title = _format_thread_title(payload)
    assert title.startswith("⚠️")


def test_title_all_skipped_cooldown_uses_pause_emoji_not_warning() -> None:
    """Healthy cooldown is not an alert — ⏸️ avoids alert-fatigue noise."""
    payload = {
        **_HEALTHY,
        "total_accepted": 0,
        "seeds_with_errors": 0,
        "seeds_total": 1,
        "seeds_skipped_cooldown": 1,
    }
    title = _format_thread_title(payload)
    assert title.startswith("⏸️")
    assert "⚠️" not in title


def test_body_healthy_includes_summary_and_paths() -> None:
    body = _format_thread_body(_HEALTHY)
    assert "Run summary" in body
    assert "expanded=`10`" in body
    assert "accepted=`10`" in body
    assert "✅ Run healthy." in body
    assert "Per-seed" in body
    assert "✅ `https://www.nature.com/nature/articles?type=article`" in body
    assert "intake:" in body
    assert "review queue:" in body


def test_body_error_path_surfaces_real_error() -> None:
    """Non-transient errors (e.g. parser_crashed) must surface as ❌ outages."""
    payload = {
        **_HEALTHY,
        "seeds_with_errors": 1,
        "seeds_with_warnings": 0,
        "total_accepted": 0,
        "summaries": [
            {
                "seed_url": "https://www.nature.com/nature/articles?type=article",
                "expanded_count": 0,
                "accepted": 0,
                "duplicate": 0,
                "rejected": 0,
                "skipped_cooldown": False,
                "error": "parser_crashed",
            }
        ],
    }
    body = _format_thread_body(payload)
    assert "❌ Some seeds failed" in body
    assert "error: `parser_crashed`" in body


def test_body_transient_warning_path_uses_calmer_verdict() -> None:
    """empty_expansion gets a transient verdict and per-seed ⚠️ marker."""
    payload = {
        **_HEALTHY,
        "seeds_with_errors": 0,
        "seeds_with_warnings": 1,
        "total_accepted": 0,
        "summaries": [
            {
                "seed_url": "https://www.nature.com/nature/articles?type=article",
                "expanded_count": 0,
                "accepted": 0,
                "duplicate": 0,
                "rejected": 0,
                "skipped_cooldown": False,
                "error": "empty_expansion",
            }
        ],
    }
    body = _format_thread_body(payload)
    assert "❌" not in body
    assert "Transient warning" in body
    assert "Cooldown was NOT advanced" in body
    assert "⚠️ `https://www.nature.com/nature/articles?type=article` — transient: `empty_expansion`" in body


def test_body_all_skipped_cooldown_path() -> None:
    payload = {
        **_HEALTHY,
        "seeds_total": 2,
        "seeds_processed": 0,
        "seeds_skipped_cooldown": 2,
        "seeds_with_errors": 0,
        "total_expanded": 0,
        "total_accepted": 0,
        "summaries": [
            {
                "seed_url": "https://a/",
                "expanded_count": 0,
                "accepted": 0,
                "duplicate": 0,
                "rejected": 0,
                "skipped_cooldown": True,
                "error": None,
            },
            {
                "seed_url": "https://b/",
                "expanded_count": 0,
                "accepted": 0,
                "duplicate": 0,
                "rejected": 0,
                "skipped_cooldown": True,
                "error": None,
            },
        ],
    }
    body = _format_thread_body(payload)
    assert "⏸️ All seeds skipped (cooldown active)" in body
    assert body.count("⏸️ `") == 2  # both per-seed lines


def test_body_includes_agent_identity_footer() -> None:
    """Channel reader must see who is reporting — even on healthy runs."""
    body = _format_thread_body(_HEALTHY)
    assert AGENT_DISPLAY_NAME in body
    assert AGENT_ID in body
    assert AGENT_DISPLAY_NAME == "집현전-경비원"
    assert AGENT_ID == "jiphyeonjeon-guard"


def test_resolve_bot_token_prefers_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """집현전-경비원 token must win when both are set so the channel author
    is the dedicated guard identity, not the bridge bot."""
    monkeypatch.setenv("DISCORD_GUARD_BOT_TOKEN", "guard-secret-123")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bridge-secret-456")

    token, source = _resolve_bot_token()

    assert token == "guard-secret-123"
    assert source == "guard"


def test_resolve_bot_token_falls_back_to_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """During the rollout window the bridge token keeps the report alive,
    but the source label must reflect the fallback for log inspection."""
    monkeypatch.delenv("DISCORD_GUARD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bridge-secret-456")

    token, source = _resolve_bot_token()

    assert token == "bridge-secret-456"
    assert source == "bridge-fallback"


def test_resolve_bot_token_raises_when_neither_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DISCORD_GUARD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    with pytest.raises(ReportConfigError):
        _resolve_bot_token()


def test_body_truncated_when_too_long() -> None:
    long_summaries = [
        {
            "seed_url": f"https://example.com/{i}",
            "expanded_count": 1,
            "accepted": 1,
            "duplicate": 0,
            "rejected": 0,
            "skipped_cooldown": False,
            "error": None,
        }
        for i in range(200)
    ]
    payload = {**_HEALTHY, "summaries": long_summaries}
    body = _format_thread_body(payload)
    assert len(body) <= 2000
    assert body.endswith("...")
