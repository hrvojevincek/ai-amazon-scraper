"""Typed HTTP client for the scraper API.

The UI imports this. So could a CLI, a Slack bot, or a notebook. Anyone who
wants to use the API in Python should go through here, not raw httpx, so the
URL paths and JSON shapes live in exactly one place.

Each method returns a plain Python dict/list — we don't re-validate with
Pydantic on the client side. The server already enforced shape; the UI just
displays whatever it gets back.
"""

from typing import Any

import httpx


class APIError(Exception):
    """Non-2xx response from the API. `.status` carries the code, `.detail` the body."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(f"API error {status}: {detail}")
        self.status = status
        self.detail = detail


class ScraperAPIClient:
    """Async client. One instance per UI session — reuses the connection pool."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # `transport` injection mirrors AmazonFetcher — lets tests use MockTransport
        # without ever opening a socket.
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ScraperAPIClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    # --- Endpoints ---

    async def health(self) -> dict[str, str]:
        return await self._get_json("/health")

    async def scrape(self, asin: str, country_code: str) -> dict[str, str]:
        return await self._post_json(
            "/scrape", {"asin": asin, "country_code": country_code}
        )

    async def list_products(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self._get_json("/products", params={"limit": limit})

    async def get_product(self, asin: str, country_code: str) -> dict[str, Any] | None:
        try:
            return await self._get_json(f"/products/{asin}/{country_code}")
        except APIError as e:
            if e.status == 404:
                return None
            raise

    async def price_history(
        self, asin: str, country_code: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        return await self._get_json(
            f"/products/{asin}/{country_code}/history", params={"limit": limit}
        )

    async def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return await self._get_json("/search", params={"q": query, "limit": limit})

    async def ask(self, question: str) -> str:
        body = await self._post_json("/ask", {"question": question})
        return body["answer"]

    # --- Internals ---

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params)
        return self._unpack(resp)

    async def _post_json(self, path: str, body: dict[str, Any]) -> Any:
        resp = await self._client.post(path, json=body)
        return self._unpack(resp)

    @staticmethod
    def _unpack(resp: httpx.Response) -> Any:
        if 200 <= resp.status_code < 300:
            return resp.json() if resp.content else None
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise APIError(resp.status_code, detail)
