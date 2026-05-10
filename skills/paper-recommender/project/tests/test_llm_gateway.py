from __future__ import annotations

import asyncio

import httpx

from paper_recommender.config import OpenClawSettings
from paper_recommender.llm import OpenClawLLM
from paper_recommender.openclaw_gateway import OpenClawGatewayPolicy


def _openclaw_settings() -> OpenClawSettings:
    return OpenClawSettings(
        base_url="http://openclaw.local/api/v1/",
        token_env="OPENCLAW_TEST_TOKEN",
        primary_model="primary",
        fallback_model="fallback",
        timeout_sec=12,
    )


def test_gateway_policy_preserves_base_path_and_auth(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_TEST_TOKEN", "secret-token")

    settings = _openclaw_settings()
    policy = OpenClawGatewayPolicy.from_values(
        base_url=settings.base_url,
        token=settings.token,
        primary_model=settings.primary_model,
        fallback_model=settings.fallback_model,
        timeout_sec=settings.timeout_sec,
        user_agent="paper-recommender/0.1",
    )

    assert policy.chat_url == "http://openclaw.local/api/v1/chat/completions"
    assert policy.headers["Authorization"] == "Bearer secret-token"
    assert policy.configured_models() == ("primary", "fallback")


def test_openclaw_llm_keeps_client_seam_and_payload_shape() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def post(self, url, json):
            self.calls.append((url, json))
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": '{"ok": true}'}}]},
            )

        async def aclose(self):
            pass

    async def run() -> tuple[object, list[tuple[str, dict]]]:
        llm = OpenClawLLM(_openclaw_settings())
        fake = FakeClient()
        llm._client = fake  # type: ignore[attr-defined]
        parsed = await llm.chat_json([{"role": "user", "content": "hi"}], temperature=0.4)
        return parsed, fake.calls

    parsed, calls = asyncio.run(run())

    assert parsed == {"ok": True}
    assert calls == [
        (
            "http://openclaw.local/api/v1/chat/completions",
            {
                "model": "primary",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 0.4,
                "response_format": {"type": "json_object"},
            },
        )
    ]
