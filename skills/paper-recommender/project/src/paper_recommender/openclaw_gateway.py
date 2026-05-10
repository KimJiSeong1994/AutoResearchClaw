from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OpenClawGatewayPolicy:
    """HTTP policy for the OpenClaw chat-completions gateway."""

    chat_url: str
    headers: dict[str, str]
    timeout_sec: float
    primary_model: str
    fallback_model: str

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
        # Build the full endpoint URL once. httpx's relative-URL resolution
        # against a base_url that already has a path is lossy (leading "/" in
        # the path strips the base path), so avoid that by using absolute URLs.
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return cls(
            chat_url=base_url.rstrip("/") + "/chat/completions",
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
        self.client = httpx.AsyncClient(
            timeout=policy.timeout_sec,
            headers=policy.headers,
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def chat_completion(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        response_format_json: bool,
    ) -> str:
        payload = _chat_payload(
            model,
            messages,
            temperature=temperature,
            response_format_json=response_format_json,
        )
        response = await self.client.post(self.policy.chat_url, json=payload)
        response.raise_for_status()
        return _chat_content(response.json())


def _chat_payload(
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    response_format_json: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format_json:
        payload["response_format"] = {"type": "json_object"}
    return payload


def _chat_content(data: Any) -> str:
    return data["choices"][0]["message"]["content"]


def short_error_text(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def failure_reason(model: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        body = short_error_text(response.text or "")
        suffix = f": {body}" if body else ""
        return f"{model}: http {response.status_code}{suffix}"
    if isinstance(exc, httpx.TimeoutException):
        return f"{model}: timeout"
    if isinstance(exc, httpx.HTTPError):
        return f"{model}: transport {exc.__class__.__name__}"
    return f"{model}: invalid response {exc.__class__.__name__}: {short_error_text(str(exc))}"
