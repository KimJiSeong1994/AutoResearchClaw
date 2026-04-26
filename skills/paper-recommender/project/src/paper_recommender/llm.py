from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from paper_recommender.config import OpenClawSettings

log = logging.getLogger(__name__)


def _short_error_text(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def _failure_reason(model: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        body = _short_error_text(response.text or "")
        suffix = f": {body}" if body else ""
        return f"{model}: http {response.status_code}{suffix}"
    if isinstance(exc, httpx.TimeoutException):
        return f"{model}: timeout"
    if isinstance(exc, httpx.HTTPError):
        return f"{model}: transport {exc.__class__.__name__}"
    return f"{model}: invalid response {exc.__class__.__name__}: {_short_error_text(str(exc))}"


class OpenClawLLM:
    def __init__(self, settings: OpenClawSettings):
        self._settings = settings
        # Build the full endpoint URL once. httpx's relative-URL resolution
        # against a base_url that already has a path is lossy (leading "/" in
        # the path strips the base path), so avoid that by using absolute URLs.
        self._chat_url = settings.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "paper-recommender/0.1",
        }
        if settings.token:
            headers["Authorization"] = f"Bearer {settings.token}"
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_sec,
            headers=headers,
        )

    async def __aenter__(self) -> "OpenClawLLM":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        response_format_json: bool = False,
    ) -> str:
        failures: list[str] = []
        for model in (self._settings.primary_model, self._settings.fallback_model):
            if not model:
                continue
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            if response_format_json:
                payload["response_format"] = {"type": "json_object"}
            try:
                r = await self._client.post(self._chat_url, json=payload)
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as e:
                reason = _failure_reason(model, e)
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
