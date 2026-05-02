"""jiphyeonjeon authentication providers.

The legacy `JiphySettings.token` reads a static JWT from the environment.
That works once but breaks after the JWT expires (~24h). The login API at
``POST /api/auth/login`` does not expose a refresh endpoint, so the
sustainable pattern for a daily cron is:

    1. Read username/password from the environment at run start
    2. POST /api/auth/login once
    3. Reuse the access_token for the duration of the run
    4. On 401 mid-run, invalidate() and let the consumer retry once
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

import httpx


class JiphyAuthError(RuntimeError):
    """Raised when login fails or the response is malformed."""


@runtime_checkable
class TokenProvider(Protocol):
    """Anything that can hand out a current access_token on demand."""

    async def get_token(self) -> str: ...


class StaticTokenProvider:
    """Wraps a pre-issued JWT (legacy ``JIPHYEONJEON_TOKEN`` env path)."""

    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("StaticTokenProvider requires a non-empty token")
        self._token = token

    async def get_token(self) -> str:
        return self._token


class LoginTokenProvider:
    """Fetches a fresh JWT on first ``get_token()`` call.

    Caches the token in memory for the process lifetime. The jiphyeonjeon
    backend exposes no refresh endpoint, so on a 401 the consumer should call
    :meth:`invalidate` and retry once — this triggers a single fresh login.
    """

    # Cap total login attempts per process to prevent infinite 401 loops
    # (and avoid an accidental brute-force pattern against the backend).
    _MAX_LOGIN_ATTEMPTS = 2

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout_sec: float = 30.0,
        *,
        _transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not username:
            raise ValueError("username is required")
        if not password:
            raise ValueError("password is required")
        self._base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._timeout_sec = timeout_sec
        self._transport = _transport
        self._token: str | None = None
        self._login_attempts = 0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        if self._token is not None:
            return self._token
        async with self._lock:
            if self._token is None:
                self._token = await self._login()
        return self._token

    def invalidate(self) -> None:
        """Drop the cached token. Next ``get_token()`` will re-login."""
        self._token = None

    async def _login(self) -> str:
        if self._login_attempts >= self._MAX_LOGIN_ATTEMPTS:
            raise JiphyAuthError(
                f"jiphyeonjeon login attempts exhausted "
                f"({self._login_attempts}/{self._MAX_LOGIN_ATTEMPTS}); "
                f"refusing to retry. Check JIPHYEONJEON_USERNAME / "
                f"JIPHYEONJEON_PASSWORD env vars."
            )
        self._login_attempts += 1

        url = f"{self._base_url}/api/auth/login"
        body = {"username": self._username, "password": self._password}
        client_kwargs: dict = {"timeout": self._timeout_sec}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(
                    url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                )
        except httpx.HTTPError as e:
            raise JiphyAuthError(
                f"jiphyeonjeon login network error: {type(e).__name__}: {e}"
            ) from e

        if resp.status_code == 401:
            raise JiphyAuthError(
                f"jiphyeonjeon login rejected (401): {self._extract_detail(resp)}"
            )
        if resp.status_code != 200:
            raise JiphyAuthError(
                f"jiphyeonjeon login failed http {resp.status_code}: "
                f"{self._extract_detail(resp)}"
            )

        try:
            data = resp.json()
        except ValueError as e:
            raise JiphyAuthError(
                f"jiphyeonjeon login returned non-json body: {e}"
            ) from e

        token = data.get("access_token") if isinstance(data, dict) else None
        if not token or not isinstance(token, str):
            raise JiphyAuthError(
                "jiphyeonjeon login response missing access_token field"
            )
        return token

    @staticmethod
    def _extract_detail(resp: httpx.Response) -> str:
        try:
            data = resp.json()
            if isinstance(data, dict):
                return str(data.get("detail") or data)
        except ValueError:
            pass
        text = resp.text or ""
        return text[:200] if text else "<empty body>"
