from __future__ import annotations

from typing import Any

import httpx

from paper_recommender.config import JiphySettings


class JiphyClient:
    def __init__(self, settings: JiphySettings):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            timeout=settings.timeout_sec,
            headers={
                "Authorization": f"Bearer {settings.token}",
                "Accept": "application/json",
                "User-Agent": "paper-recommender/0.1",
            },
        )

    async def __aenter__(self) -> "JiphyClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def list_bookmarks(self) -> list[dict[str, Any]]:
        r = await self._client.get("/api/bookmarks")
        r.raise_for_status()
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

        r = await self._client.post("/api/search", json=body)
        r.raise_for_status()
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
        r = await self._client.post(
            f"/api/bookmarks/{bookmark_id}/citation-tree",
            json={"depth": depth, "max_per_direction": max_per_direction},
        )
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {"tree": data}
