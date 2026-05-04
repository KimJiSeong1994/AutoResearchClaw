from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class OpenClawClient:
    base_url: str
    token: str
    model: str
    timeout_sec: float

    async def chat(self, prompt: str) -> str:
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are OpenClaw operating inside an allowlisted Discord channel. "
                        "Answer concisely, avoid exposing secrets, and ask for clarification only when necessary."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            response = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OpenClaw returned an unexpected chat/completions shape") from exc
        return str(content).strip() or "(empty response)"

    async def health(self) -> str:
        headers = {"Authorization": f"Bearer {self.token}"}
        async with httpx.AsyncClient(timeout=min(self.timeout_sec, 20)) as client:
            response = await client.get(f"{self.base_url}/models", headers=headers)
            response.raise_for_status()
        return "ok"
