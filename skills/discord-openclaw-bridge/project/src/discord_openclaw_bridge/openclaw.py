from __future__ import annotations

from dataclasses import dataclass

from .openclaw_gateway import OpenClawGatewayClient, OpenClawGatewayPolicy


_SYSTEM_PROMPT = (
    "You are OpenClaw operating inside an allowlisted Discord channel. "
    "Answer concisely, avoid exposing secrets, and ask for clarification only when necessary."
)


@dataclass(frozen=True)
class OpenClawClient:
    base_url: str
    token: str
    model: str
    timeout_sec: float

    def _policy(self, *, timeout_sec: float | None = None) -> OpenClawGatewayPolicy:
        return OpenClawGatewayPolicy.from_values(
            base_url=self.base_url,
            token=self.token,
            primary_model=self.model,
            timeout_sec=self.timeout_sec if timeout_sec is None else timeout_sec,
            user_agent="discord-openclaw-bridge/0.1",
        )

    async def chat(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        async with OpenClawGatewayClient(self._policy()) as gateway:
            try:
                content = await gateway.chat_completion(self.model, messages)
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError("OpenClaw returned an unexpected chat/completions shape") from exc
        return str(content).strip() or "(empty response)"

    async def health(self) -> str:
        async with OpenClawGatewayClient(self._policy(timeout_sec=min(self.timeout_sec, 20))) as gateway:
            return await gateway.models_health()
