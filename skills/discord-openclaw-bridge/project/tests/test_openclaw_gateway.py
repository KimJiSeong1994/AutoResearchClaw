from __future__ import annotations

from discord_openclaw_bridge.openclaw_gateway import (
    OpenClawGatewayPolicy,
    chat_payload,
    is_loopback_base_url,
)


def test_gateway_policy_preserves_v1_base_path_and_auth() -> None:
    policy = OpenClawGatewayPolicy.from_values(
        base_url="http://127.0.0.1:18789/v1/",
        token="gateway-token",
        primary_model="openclaw/clawbridge",
        timeout_sec=7,
        user_agent="discord-openclaw-bridge/test",
    )

    assert policy.chat_url == "http://127.0.0.1:18789/v1/chat/completions"
    assert policy.models_url == "http://127.0.0.1:18789/v1/models"
    assert policy.headers["Authorization"] == "Bearer gateway-token"
    assert policy.headers["User-Agent"] == "discord-openclaw-bridge/test"
    assert policy.configured_models() == ("openclaw/clawbridge",)


def test_chat_payload_supports_discord_dedupe_options() -> None:
    payload = chat_payload(
        "openclaw/clawbridge",
        [{"role": "user", "content": "hi"}],
        temperature=0,
        max_tokens=400,
    )

    assert payload == {
        "model": "openclaw/clawbridge",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
        "max_tokens": 400,
    }


def test_chat_payload_omits_temperature_by_default() -> None:
    payload = chat_payload(
        "openclaw/clawbridge",
        [{"role": "user", "content": "hi"}],
    )

    assert payload == {
        "model": "openclaw/clawbridge",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_loopback_policy_matches_bridge_allowlist() -> None:
    assert is_loopback_base_url("http://127.0.0.1:18789/v1")
    assert is_loopback_base_url("http://localhost:18789/v1")
    assert is_loopback_base_url("http://[::1]:18789/v1")
    assert not is_loopback_base_url("https://example.com/v1")
    assert not is_loopback_base_url("http://127.0.0.1:18789@evil.example/v1")
    assert not is_loopback_base_url("http://localhost:18789@evil.example/v1")
    assert not is_loopback_base_url("http://user@localhost:18789/v1")
    assert not is_loopback_base_url("http://127.0.0.1/v1")
    assert not is_loopback_base_url("http://127.0.0.1:bad/v1")
    assert not is_loopback_base_url("http://127.0.0.1:99999/v1")
