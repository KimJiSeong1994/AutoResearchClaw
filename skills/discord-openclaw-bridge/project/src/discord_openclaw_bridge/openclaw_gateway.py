from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class OpenClawGatewayPolicy:
    """HTTP policy for the OpenClaw-compatible gateway.

    This mirrors the Phase-2 paper-recommender gateway contract while keeping
    Discord-specific config and safety decisions at the call sites.
    """

    base_url: str
    chat_url: str
    models_url: str
    headers: dict[str, str]
    timeout_sec: float
    primary_model: str
    fallback_model: str = ""

    @classmethod
    def from_values(
        cls,
        *,
        base_url: str,
        token: str = "",
        primary_model: str,
        fallback_model: str = "",
        timeout_sec: float,
        user_agent: str,
    ) -> "OpenClawGatewayPolicy":
        normalized = base_url.strip().rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return cls(
            base_url=normalized,
            chat_url=normalized + "/chat/completions",
            models_url=normalized + "/models",
            headers=headers,
            timeout_sec=timeout_sec,
            primary_model=primary_model,
            fallback_model=fallback_model,
        )

    def configured_models(self) -> tuple[str, ...]:
        return tuple(model for model in (self.primary_model, self.fallback_model) if model)


class OpenClawGatewayClient:
    def __init__(self, policy: OpenClawGatewayPolicy):
        self.policy = policy
        self.client = httpx.AsyncClient(timeout=policy.timeout_sec, headers=policy.headers)

    async def __aenter__(self) -> "OpenClawGatewayClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format_json: bool = False,
    ) -> str:
        payload = chat_payload(
            model,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format_json=response_format_json,
        )
        response = await self.client.post(self.policy.chat_url, json=payload)
        response.raise_for_status()
        return chat_content(response.json())

    async def models_health(self) -> str:
        response = await self.client.get(self.policy.models_url)
        response.raise_for_status()
        return "ok"


def chat_payload(
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format_json: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}
    return payload


def chat_content(data: Any) -> str:
    return data["choices"][0]["message"]["content"]


def is_loopback_base_url(base_url: str) -> bool:
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return False
    if parsed.scheme != "http":
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        return parsed.port is not None
    except ValueError:
        return False
