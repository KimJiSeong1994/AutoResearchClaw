from __future__ import annotations

from typing import Any

import httpx

from paper_recommender.config import JiphySettings
from paper_recommender.jiphyeonjeon_auth import TokenProvider


class JiphyClient:
    """Async client for the jiphyeonjeon backend.

    Two auth modes:

    - **Legacy static token** — when ``token_provider`` is None the client
      reads a single JWT from ``settings.token`` (which itself reads the
      env var named in ``settings.token_env``) and bakes it into the
      ``Authorization`` header at construction. Existing daily/weekly
      pipelines use this path.
    - **TokenProvider** — when a provider is supplied, the client pulls a
      fresh token via :meth:`TokenProvider.get_token` before each request.
      On a 401 the provider is invalidated and the request is retried once.
      The new ``daily_research`` pipeline uses this path with a
      ``LoginTokenProvider`` so the JWT is refreshed via login at the start
      of each cron run.
    """

    def __init__(
        self,
        settings: JiphySettings,
        token_provider: TokenProvider | None = None,
    ) -> None:
        self._settings = settings
        self._provider = token_provider

        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "paper-recommender/0.1",
        }
        if token_provider is None:
            headers["Authorization"] = f"Bearer {settings.token}"

        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            timeout=settings.timeout_sec,
            headers=headers,
        )

    async def __aenter__(self) -> "JiphyClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def _authed_request(
        self,
        method: str,
        path: str,
        **kw: Any,
    ) -> httpx.Response:
        """Send a request, injecting a fresh token if a provider is wired.

        On 401 with a provider, invalidate the cached token and retry exactly
        once. The single retry prevents infinite loops on stale credentials —
        the provider's own attempt cap (``LoginTokenProvider._MAX_LOGIN_ATTEMPTS``)
        handles the worst case.
        """

        if self._provider is not None:
            kw.setdefault("headers", {})
            token = await self._provider.get_token()
            kw["headers"]["Authorization"] = f"Bearer {token}"

        resp = await self._client.request(method, path, **kw)

        if resp.status_code == 401 and self._provider is not None:
            self._provider.invalidate()
            new_token = await self._provider.get_token()
            kw["headers"]["Authorization"] = f"Bearer {new_token}"
            resp = await self._client.request(method, path, **kw)

        resp.raise_for_status()
        return resp

    async def list_bookmarks(self) -> list[dict[str, Any]]:
        r = await self._authed_request("GET", "/api/bookmarks")
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("bookmarks", data.get("items", []))

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_start: int | None = None,
        year_end: int | None = None,
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "query": query,
            "max_results": max_results,
            "fast_mode": True,
            "save_papers": False,
        }
        if year_start is not None:
            body["year_start"] = year_start
        if year_end is not None:
            body["year_end"] = year_end

        r = await self._authed_request("POST", "/api/search", json=body)
        data = r.json()

        grouped = data.get("results") if isinstance(data, dict) else None
        if isinstance(grouped, dict):
            flat: list[dict[str, Any]] = []
            for src, papers in grouped.items():
                if isinstance(papers, list):
                    for p in papers:
                        if isinstance(p, dict):
                            p.setdefault("source", src)
                            flat.append(p)
            return flat
        if isinstance(data, dict) and isinstance(data.get("papers"), list):
            return data["papers"]
        return []

    async def citation_tree(
        self,
        bookmark_id: str,
        depth: int = 2,
        max_per_direction: int = 10,
    ) -> dict[str, Any]:
        r = await self._authed_request(
            "POST",
            f"/api/bookmarks/{bookmark_id}/citation-tree",
            json={"depth": depth, "max_per_direction": max_per_direction},
        )
        data = r.json()
        return data if isinstance(data, dict) else {"tree": data}
