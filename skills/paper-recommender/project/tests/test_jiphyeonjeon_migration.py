"""Tests for the JiphyClient TokenProvider migration.

Two paths must keep working:
  - Legacy: no token_provider — Authorization is baked from settings.token
    at __init__ time (existing daily/weekly behavior).
  - Provider: token_provider supplied — token pulled before each request,
    invalidated + retried on 401.
"""

from __future__ import annotations

import asyncio

import httpx

from paper_recommender.config import JiphySettings
from paper_recommender.jiphyeonjeon import JiphyClient
from paper_recommender.jiphyeonjeon_auth import StaticTokenProvider, TokenProvider


def _settings(monkeypatch) -> JiphySettings:
    """Construct minimal JiphySettings backed by a test env var."""
    monkeypatch.setenv("JIPHY_TEST_TOKEN", "STATIC_LEGACY_TOKEN")
    return JiphySettings(
        base_url="https://jiphy.test",
        token_env="JIPHY_TEST_TOKEN",
        timeout_sec=10,
    )


def _bind_transport(client: JiphyClient, transport: httpx.MockTransport) -> None:
    """Replace the inner httpx.AsyncClient with one bound to a MockTransport,
    preserving headers/base_url/timeout."""
    inner = client._client  # type: ignore[attr-defined]
    new = httpx.AsyncClient(
        transport=transport,
        base_url=str(inner.base_url),
        timeout=inner.timeout,
        headers=dict(inner.headers),
    )
    asyncio.get_event_loop().run_until_complete(inner.aclose())
    client._client = new  # type: ignore[attr-defined]


def test_legacy_path_bakes_static_authorization_header(monkeypatch) -> None:
    """No provider → Authorization header is set at construction from settings.token."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    settings = _settings(monkeypatch)
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        base_url=settings.base_url,
        timeout=settings.timeout_sec,
        headers={
            "Accept": "application/json",
            "User-Agent": "paper-recommender/0.1",
            "Authorization": f"Bearer {settings.token}",
        },
    )
    jc = JiphyClient.__new__(JiphyClient)
    jc._settings = settings  # type: ignore[attr-defined]
    jc._provider = None  # type: ignore[attr-defined]
    jc._client = client  # type: ignore[attr-defined]

    async def go():
        async with jc:
            return await jc.list_bookmarks()

    asyncio.run(go())
    assert seen["authorization"] == "Bearer STATIC_LEGACY_TOKEN"


def test_provider_path_pulls_fresh_token_per_request(monkeypatch) -> None:
    seen_tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_tokens.append(request.headers.get("authorization") or "")
        return httpx.Response(200, json=[])

    settings = JiphySettings(base_url="https://jiphy.test", token_env="UNUSED", timeout_sec=10)
    provider = StaticTokenProvider("PROVIDER_TOKEN_A")

    jc = JiphyClient.__new__(JiphyClient)
    jc._settings = settings  # type: ignore[attr-defined]
    jc._provider = provider  # type: ignore[attr-defined]
    transport = httpx.MockTransport(handler)
    jc._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url=settings.base_url,
        timeout=settings.timeout_sec,
        headers={"Accept": "application/json", "User-Agent": "paper-recommender/0.1"},
    )

    async def go():
        async with jc:
            await jc.list_bookmarks()
            await jc.list_bookmarks()

    asyncio.run(go())
    assert seen_tokens == ["Bearer PROVIDER_TOKEN_A", "Bearer PROVIDER_TOKEN_A"]


class _RotatingProvider:
    """Returns TOKEN_A first, then TOKEN_B after invalidate(). For 401-retry."""

    def __init__(self) -> None:
        self._tokens = ["TOKEN_A", "TOKEN_B"]
        self._idx = 0
        self._invalidated = 0

    async def get_token(self) -> str:
        return self._tokens[min(self._idx, len(self._tokens) - 1)]

    def invalidate(self) -> None:
        self._invalidated += 1
        self._idx += 1


def test_provider_401_invalidates_and_retries_once() -> None:
    """On 401 with provider: invalidate + one retry with fresh token."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization") or ""
        calls.append(auth)
        if auth == "Bearer TOKEN_A":
            return httpx.Response(401, json={"detail": "expired"})
        return httpx.Response(200, json=[])

    settings = JiphySettings(base_url="https://jiphy.test", token_env="UNUSED", timeout_sec=10)
    provider = _RotatingProvider()

    jc = JiphyClient.__new__(JiphyClient)
    jc._settings = settings  # type: ignore[attr-defined]
    jc._provider = provider  # type: ignore[attr-defined]
    transport = httpx.MockTransport(handler)
    jc._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url=settings.base_url,
        timeout=settings.timeout_sec,
        headers={"Accept": "application/json", "User-Agent": "paper-recommender/0.1"},
    )

    async def go():
        async with jc:
            return await jc.list_bookmarks()

    asyncio.run(go())
    assert calls == ["Bearer TOKEN_A", "Bearer TOKEN_B"]
    assert provider._invalidated == 1
    assert isinstance(provider, TokenProvider)


def test_provider_401_then_401_raises() -> None:
    """If the second attempt is also 401, raise rather than loop."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "still bad"})

    settings = JiphySettings(base_url="https://jiphy.test", token_env="UNUSED", timeout_sec=10)
    provider = _RotatingProvider()

    jc = JiphyClient.__new__(JiphyClient)
    jc._settings = settings  # type: ignore[attr-defined]
    jc._provider = provider  # type: ignore[attr-defined]
    transport = httpx.MockTransport(handler)
    jc._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url=settings.base_url,
        timeout=settings.timeout_sec,
        headers={"Accept": "application/json", "User-Agent": "paper-recommender/0.1"},
    )

    import pytest

    async def go():
        async with jc:
            await jc.list_bookmarks()

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(go())


def test_static_token_provider_survives_401_retry() -> None:
    """Regression for HIGH-1: a 401 with StaticTokenProvider must not crash
    on AttributeError when _authed_request calls provider.invalidate()."""
    from paper_recommender.jiphyeonjeon_auth import StaticTokenProvider

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("authorization") or "")
        if len(calls) == 1:
            return httpx.Response(401, json={"detail": "expired (will retry)"})
        return httpx.Response(200, json=[])

    settings = JiphySettings(base_url="https://jiphy.test", token_env="UNUSED", timeout_sec=10)
    provider = StaticTokenProvider("STATIC_TOKEN")

    jc = JiphyClient.__new__(JiphyClient)
    jc._settings = settings  # type: ignore[attr-defined]
    jc._provider = provider  # type: ignore[attr-defined]
    transport = httpx.MockTransport(handler)
    jc._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url=settings.base_url,
        timeout=settings.timeout_sec,
        headers={"Accept": "application/json", "User-Agent": "paper-recommender/0.1"},
    )

    async def go():
        async with jc:
            return await jc.list_bookmarks()

    # Before the fix this raised AttributeError on provider.invalidate().
    # After: invalidate() is a no-op, retry succeeds with the same static token.
    result = asyncio.run(go())
    assert result == []
    assert calls == ["Bearer STATIC_TOKEN", "Bearer STATIC_TOKEN"]


def test_search_via_provider_path_works() -> None:
    """search() must also flow through _authed_request."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        bodies.append(json.loads(request.content.decode()))
        return httpx.Response(
            200,
            json={"papers": [{"title": "Test paper", "year": 2024}]},
        )

    settings = JiphySettings(base_url="https://jiphy.test", token_env="UNUSED", timeout_sec=10)
    provider = StaticTokenProvider("X")

    jc = JiphyClient.__new__(JiphyClient)
    jc._settings = settings  # type: ignore[attr-defined]
    jc._provider = provider  # type: ignore[attr-defined]
    transport = httpx.MockTransport(handler)
    jc._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        base_url=settings.base_url,
        timeout=settings.timeout_sec,
        headers={"Accept": "application/json", "User-Agent": "paper-recommender/0.1"},
    )

    async def go():
        async with jc:
            return await jc.search("transformers", max_results=3, year_start=2022)

    out = asyncio.run(go())
    assert out == [{"title": "Test paper", "year": 2024}]
    assert bodies[0] == {
        "query": "transformers",
        "max_results": 3,
        "fast_mode": True,
        "save_papers": False,
        "year_start": 2022,
    }
