from __future__ import annotations

import asyncio

from discord_openclaw_bridge.openclaw import OpenClawClient


def test_openclaw_client_chat_uses_gateway_policy_and_system_prompt(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeGatewayClient:
        def __init__(self, policy):
            self.policy = policy

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def chat_completion(self, model, messages, *, temperature=None, max_tokens=None, response_format_json=False):
            calls.append({
                "url": self.policy.chat_url,
                "headers": self.policy.headers,
                "model": model,
                "messages": messages,
                "temperature": temperature,
            })
            return "  hello  "

    import discord_openclaw_bridge.openclaw as openclaw_module

    monkeypatch.setattr(openclaw_module, "OpenClawGatewayClient", FakeGatewayClient)

    client = OpenClawClient(
        base_url="http://127.0.0.1:18789/v1",
        token="gateway-token",
        model="openclaw/clawbridge",
        timeout_sec=3,
    )
    answer = asyncio.run(client.chat("hi"))

    assert answer == "hello"
    assert calls[0]["url"] == "http://127.0.0.1:18789/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer gateway-token"
    assert calls[0]["model"] == "openclaw/clawbridge"
    assert calls[0]["temperature"] is None
    messages = calls[0]["messages"]
    assert messages[0]["role"] == "system"
    assert "allowlisted Discord channel" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "hi"}


def test_openclaw_client_chat_preserves_unexpected_shape_error(monkeypatch) -> None:
    class FakeGatewayClient:
        def __init__(self, policy):
            self.policy = policy

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def chat_completion(self, model, messages, *, temperature=0, max_tokens=None, response_format_json=False):
            raise KeyError("choices")

    import discord_openclaw_bridge.openclaw as openclaw_module

    monkeypatch.setattr(openclaw_module, "OpenClawGatewayClient", FakeGatewayClient)

    client = OpenClawClient(
        base_url="http://127.0.0.1:18789/v1",
        token="gateway-token",
        model="openclaw/clawbridge",
        timeout_sec=3,
    )

    try:
        asyncio.run(client.chat("hi"))
    except RuntimeError as exc:
        assert str(exc) == "OpenClaw returned an unexpected chat/completions shape"
        assert isinstance(exc.__cause__, KeyError)
    else:
        raise AssertionError("expected RuntimeError for malformed gateway response")


def test_openclaw_client_health_uses_shorter_gateway_timeout(monkeypatch) -> None:
    timeouts: list[float] = []

    class FakeGatewayClient:
        def __init__(self, policy):
            self.policy = policy
            timeouts.append(policy.timeout_sec)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def models_health(self):
            return "ok"

    import discord_openclaw_bridge.openclaw as openclaw_module

    monkeypatch.setattr(openclaw_module, "OpenClawGatewayClient", FakeGatewayClient)

    client = OpenClawClient(
        base_url="http://127.0.0.1:18789/v1",
        token="gateway-token",
        model="openclaw/clawbridge",
        timeout_sec=120,
    )

    assert asyncio.run(client.health()) == "ok"
    assert timeouts == [20]
