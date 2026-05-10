from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from paper_recommender.config import OpenClawSettings
from paper_recommender.openclaw_gateway import (
    OpenClawGatewayClient,
    OpenClawGatewayPolicy,
    failure_reason,
)

log = logging.getLogger(__name__)


class OpenClawLLM:
    def __init__(self, settings: OpenClawSettings):
        self._settings = settings
        policy = OpenClawGatewayPolicy.from_values(
            base_url=settings.base_url,
            token=settings.token,
            primary_model=settings.primary_model,
            fallback_model=settings.fallback_model,
            timeout_sec=settings.timeout_sec,
            user_agent="paper-recommender/0.1",
        )
        self._gateway = OpenClawGatewayClient(policy)
        self._chat_url = self._gateway.policy.chat_url

    @property
    def _client(self):
        return self._gateway.client

    @_client.setter
    def _client(self, client) -> None:
        # Preserve the historical test/seam where callers replace the underlying
        # AsyncClient directly on OpenClawLLM.
        self._gateway.client = client

    async def __aenter__(self) -> "OpenClawLLM":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._gateway.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        response_format_json: bool = False,
    ) -> str:
        failures: list[str] = []
        for model in self._gateway.policy.configured_models():
            try:
                return await self._gateway.chat_completion(
                    model,
                    messages,
                    temperature=temperature,
                    response_format_json=response_format_json,
                )
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
                reason = failure_reason(model, e)
                failures.append(reason)
                log.warning("OpenClaw model failed: %s", reason)
                continue
        detail = "; ".join(failures) if failures else "no models configured"
        raise RuntimeError(f"all OpenClaw models failed ({detail})")

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
    ) -> Any:
        raw = await self.chat(messages, temperature=temperature, response_format_json=True)
        return _coerce_json(raw)


def _coerce_json(text: str) -> Any:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
