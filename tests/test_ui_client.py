"""Tests for the API client.

Uses httpx.MockTransport — same pattern we used in test_fetcher. Lets us
assert the client sends the right URLs/bodies and unpacks responses correctly,
without ever spinning up the FastAPI app. End-to-end (UI → API) is covered by
the integration tests in Step 11.
"""

import json

import httpx
import pytest

from new_amazon_scraper.ui_client import APIError, ScraperAPIClient


def _make_client(handler) -> ScraperAPIClient:
    return ScraperAPIClient(transport=httpx.MockTransport(handler))


class TestRequests:
    async def test_health(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/health"
            return httpx.Response(200, json={"status": "ok"})

        async with _make_client(handler) as client:
            assert await client.health() == {"status": "ok"}

    async def test_scrape_posts_json_body(self):
        seen: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                202,
                json={"asin": "B08N5WRWNW", "country_code": "US", "status": "queued"},
            )

        async with _make_client(handler) as client:
            result = await client.scrape("B08N5WRWNW", "US")

        assert seen["path"] == "/scrape"
        assert seen["body"] == {"asin": "B08N5WRWNW", "country_code": "US"}
        assert result["status"] == "queued"

    async def test_list_products_passes_limit(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["limit"] == "25"
            return httpx.Response(200, json=[{"asin": "B08N5WRWNW"}])

        async with _make_client(handler) as client:
            assert await client.list_products(limit=25) == [{"asin": "B08N5WRWNW"}]

    async def test_get_product_hit(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/products/B08N5WRWNW/US"
            return httpx.Response(200, json={"asin": "B08N5WRWNW", "title": "Echo"})

        async with _make_client(handler) as client:
            product = await client.get_product("B08N5WRWNW", "US")
            assert product["title"] == "Echo"

    async def test_get_product_miss_returns_none(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "not found"})

        async with _make_client(handler) as client:
            assert await client.get_product("B00NOTFOUND", "US") is None

    async def test_search_passes_query_params(self):
        seen: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["q"] = request.url.params["q"]
            seen["limit"] = request.url.params["limit"]
            return httpx.Response(200, json=[{"asin": "B08N5WRWNW", "score": 0.9}])

        async with _make_client(handler) as client:
            hits = await client.search("speaker", limit=5)

        assert seen == {"q": "speaker", "limit": "5"}
        assert hits[0]["score"] == 0.9

    async def test_ask_extracts_answer(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"answer": "It costs $49.99."})

        async with _make_client(handler) as client:
            answer = await client.ask("How much?")
            assert answer == "It costs $49.99."

    async def test_price_history(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/products/B08N5WRWNW/US/history"
            return httpx.Response(200, json=[{"price": "49.99"}])

        async with _make_client(handler) as client:
            history = await client.price_history("B08N5WRWNW", "US")
            assert history == [{"price": "49.99"}]


class TestErrors:
    async def test_5xx_raises_api_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"detail": "boom"})

        async with _make_client(handler) as client:
            with pytest.raises(APIError) as exc:
                await client.health()
            assert exc.value.status == 500
            assert exc.value.detail == {"detail": "boom"}

    async def test_validation_error_carries_body(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": [{"msg": "bad asin"}]})

        async with _make_client(handler) as client:
            with pytest.raises(APIError) as exc:
                await client.scrape("nope", "US")
            assert exc.value.status == 422

    async def test_non_json_error_falls_back_to_text(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="Bad Gateway")

        async with _make_client(handler) as client:
            with pytest.raises(APIError) as exc:
                await client.health()
            assert exc.value.detail == "Bad Gateway"
