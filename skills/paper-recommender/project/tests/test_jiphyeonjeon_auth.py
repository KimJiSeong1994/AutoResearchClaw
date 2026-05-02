from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from paper_recommender.jiphyeonjeon_auth import (
    JiphyAuthError,
    LoginTokenProvider,
    StaticTokenProvider,
    TokenProvider,
)


def test_static_token_provider_returns_constant() -> None:
    p = StaticTokenProvider("abc.def.ghi")
    assert asyncio.run(p.get_token()) == "abc.def.ghi"
    assert isinstance(p, TokenProvider)


def test_static_token_provider_rejects_empty() -> None:
    with pytest.raises(ValueError):
        StaticTokenProvider("")


def test_login_token_provider_validates_inputs() -> None:
    with pytest.raises(ValueError):
        LoginTokenProvider(base_url="", username="u", password="p")
    with pytest.raises(ValueError):
        LoginTokenProvider(base_url="https://x", username="", password="p")
    with pytest.raises(ValueError):
        LoginTokenProvider(base_url="https://x", username="u", password="")


def _make_provider(handler) -> LoginTokenProvider:
    transport = httpx.MockTransport(handler)
    return LoginTokenProvider(
        base_url="https://jiphyeonjeon.kr",
        username="alice",
        password="hunter2",
        _transport=transport,
    )


def test_login_success_returns_access_token_and_sends_correct_request() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content.decode())
        captured["content_type"] = request.headers.get("content-type")
        return httpx.Response(
            200,
            json={"access_token": "JWT_OK", "expires_in": 86400},
        )

    p = _make_provider(handler)
    token = asyncio.run(p.get_token())

    assert token == "JWT_OK"
    assert captured["url"] == "https://jiphyeonjeon.kr/api/auth/login"
    assert captured["method"] == "POST"
    assert captured["body"] == {"username": "alice", "password": "hunter2"}
    assert "application/json" in captured["content_type"].lower()


def test_login_caches_token_across_calls() -> None:
    call_count = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": f"JWT_{call_count['n']}"})

    p = _make_provider(handler)
    t1 = asyncio.run(p.get_token())
    t2 = asyncio.run(p.get_token())
    t3 = asyncio.run(p.get_token())
    assert t1 == t2 == t3 == "JWT_1"
    assert call_count["n"] == 1


def test_invalidate_forces_relogin() -> None:
    call_count = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"access_token": f"JWT_{call_count['n']}"})

    p = _make_provider(handler)
    assert asyncio.run(p.get_token()) == "JWT_1"
    p.invalidate()
    assert asyncio.run(p.get_token()) == "JWT_2"
    assert call_count["n"] == 2


def test_login_401_raises_with_detail() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "wrong password"})

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    msg = str(exc_info.value)
    assert "401" in msg
    assert "wrong password" in msg


def test_login_non_200_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="gateway down")

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    assert "503" in str(exc_info.value)


def test_login_missing_access_token_field_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "wrong-field-name"})

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    assert "access_token" in str(exc_info.value)


def test_login_non_json_body_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError):
        asyncio.run(p.get_token())


def test_extract_detail_redacts_credential_shaped_text() -> None:
    """If the backend echoes a token/password in its error body we must not
    plant it in our exception messages or logs."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"detail": "rejected: password=hunter2 token=abc.def.ghi"},
        )

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    msg = str(exc_info.value)
    assert "[REDACTED]" in msg
    assert "hunter2" not in msg
    assert "abc.def.ghi" not in msg


def test_extract_detail_redacts_when_body_is_plain_text() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="error: bearer xxxxx.yyyy.zzz expired")

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    msg = str(exc_info.value)
    assert "[REDACTED]" in msg
    assert "xxxxx.yyyy.zzz" not in msg


def test_login_network_error_raises_with_clear_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failure")

    p = _make_provider(handler)
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    assert "network" in str(exc_info.value).lower()


def test_login_attempts_capped_at_max() -> None:
    """After MAX_LOGIN_ATTEMPTS calls (success or failure), refuse further logins.

    Prevents both infinite 401 loops on stale credentials AND accidental
    brute-force patterns against the backend.
    """

    def always_401(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "wrong creds"})

    p = _make_provider(always_401)
    # Use the explicit cap so the test stays correct if MAX_LOGIN_ATTEMPTS changes.
    cap = LoginTokenProvider._MAX_LOGIN_ATTEMPTS
    assert cap >= 1

    # Each invalidate+get_token cycle hits _login() exactly once.
    for _ in range(cap):
        with pytest.raises(JiphyAuthError):
            asyncio.run(p.get_token())
        p.invalidate()

    # Now we're at the cap. The next attempt should refuse without hitting the API.
    with pytest.raises(JiphyAuthError) as exc_info:
        asyncio.run(p.get_token())
    msg = str(exc_info.value).lower()
    assert "exhausted" in msg or "refusing" in msg


def test_static_token_provider_invalidate_is_noop() -> None:
    """``invalidate()`` must exist on the static path so 401-retry doesn't crash."""
    p = StaticTokenProvider("STATIC")
    p.invalidate()  # must not raise
    # Token unchanged — static provider has no cache to invalidate.
    assert asyncio.run(p.get_token()) == "STATIC"


def test_login_token_provider_satisfies_protocol_with_invalidate() -> None:
    p = LoginTokenProvider(base_url="https://x", username="u", password="p")
    assert isinstance(p, TokenProvider)
    # invalidate is part of the Protocol contract now.
    assert hasattr(p, "invalidate")
    p.invalidate()  # must not raise


def test_login_attempts_counter_does_not_increment_on_cached_hit() -> None:
    """A cached token hit must NOT consume a login attempt."""
    n_calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        n_calls["n"] += 1
        return httpx.Response(200, json={"access_token": "OK"})

    p = _make_provider(handler)
    asyncio.run(p.get_token())
    asyncio.run(p.get_token())
    asyncio.run(p.get_token())
    assert n_calls["n"] == 1
    assert p._login_attempts == 1  # only the actual login counted
