"""A thin async HTTP client the verifier drives the target app with.

The verifier is a *client*: it behaves exactly like a browser/user would and never
executes the target's code. An optional transport lets the bench and tests run a
control app in-process (httpx ASGITransport) with no server or port.
"""

from __future__ import annotations

from typing import Any

import httpx


class Client:
    def __init__(
        self,
        base_url: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._default_headers = dict(default_headers or {})
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
        )

    async def request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        merged: dict[str, str] = dict(self._default_headers)
        if headers:
            merged.update(headers)
        if token:
            merged["Authorization"] = f"Bearer {token}"
        return await self._client.request(method, path, headers=merged, json=json)

    async def aclose(self) -> None:
        await self._client.aclose()
