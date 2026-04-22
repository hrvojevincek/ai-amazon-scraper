"""Tests for fetcher.py.

httpx.MockTransport replaces the network. Retries use a zero-delay backoff
so tests run instantly. Every behavior (URL building, retry, client-vs-server
errors, exhaustion) is exercised without hitting anything real.
"""

import json

import httpx
import pytest

from new_amazon_scraper.fetcher import AmazonFetcher, BrightDataFetcher, FetchError


def _fetcher(handler, max_retries: int = 2) -> AmazonFetcher:
    return AmazonFetcher(
        transport=httpx.MockTransport(handler),
        max_retries=max_retries,
        backoff=lambda _: 0,  # no real sleep in tests
    )


class TestUrlBuilding:
    async def test_us_maps_to_amazon_com(self):
        seen: list[str] = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, text="<html></html>")

        async with _fetcher(handler) as f:
            await f.fetch_html("B08N5WRWNW", "US")
        assert seen == ["https://amazon.com/dp/B08N5WRWNW"]

    async def test_uk_maps_to_co_uk(self):
        seen: list[str] = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, text="<html></html>")

        async with _fetcher(handler) as f:
            await f.fetch_html("B08N5WRWNW", "GB")
        assert "amazon.co.uk" in seen[0]

    async def test_country_code_is_case_insensitive(self):
        seen: list[str] = []

        def handler(req):
            seen.append(str(req.url))
            return httpx.Response(200, text="<html></html>")

        async with _fetcher(handler) as f:
            await f.fetch_html("B08N5WRWNW", "de")
        assert "amazon.de" in seen[0]

    async def test_unknown_country_raises(self):
        def handler(req):
            return httpx.Response(200)

        async with _fetcher(handler) as f:
            with pytest.raises(FetchError, match="Unsupported country"):
                await f.fetch_html("B08N5WRWNW", "ZZ")


class TestRetries:
    async def test_retries_5xx_then_succeeds(self):
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(503)
            return httpx.Response(200, text="<html>ok</html>")

        async with _fetcher(handler, max_retries=2) as f:
            html = await f.fetch_html("B08N5WRWNW", "US")
        assert html == "<html>ok</html>"
        assert calls == 2

    async def test_gives_up_after_exhausting_retries(self):
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            return httpx.Response(503)

        async with _fetcher(handler, max_retries=1) as f:
            with pytest.raises(FetchError, match="fetch failed"):
                await f.fetch_html("B08N5WRWNW", "US")
        assert calls == 2  # initial attempt + 1 retry

    async def test_4xx_fails_fast_no_retry(self):
        # 404 is permanent; retrying wastes the scraping budget.
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            return httpx.Response(404)

        async with _fetcher(handler, max_retries=3) as f:
            with pytest.raises(FetchError, match="client error 404"):
                await f.fetch_html("B08N5WRWNW", "US")
        assert calls == 1

    async def test_timeout_is_retried(self):
        calls = 0

        def handler(req):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ConnectTimeout("simulated")
            return httpx.Response(200, text="<html>ok</html>")

        async with _fetcher(handler, max_retries=2) as f:
            html = await f.fetch_html("B08N5WRWNW", "US")
        assert html == "<html>ok</html>"
        assert calls == 2


class TestHappyPath:
    async def test_returns_body_text(self):
        def handler(req):
            return httpx.Response(200, text="<html>ok</html>")

        async with _fetcher(handler) as f:
            html = await f.fetch_html("B08N5WRWNW", "US")
        assert html == "<html>ok</html>"

    async def test_sends_browser_user_agent(self):
        headers_seen: list[str] = []

        def handler(req):
            headers_seen.append(req.headers.get("user-agent", ""))
            return httpx.Response(200, text="")

        async with _fetcher(handler) as f:
            await f.fetch_html("B08N5WRWNW", "US")
        assert "Mozilla" in headers_seen[0]


def _bd_fetcher(handler) -> BrightDataFetcher:
    return BrightDataFetcher(
        api_token="t",
        zone="z",
        transport=httpx.MockTransport(handler),
    )


class TestBrightDataFetcher:
    async def test_posts_to_bd_endpoint_with_amazon_url(self):
        seen: list[dict] = []

        def handler(req):
            seen.append(json.loads(req.content))
            return httpx.Response(
                200,
                json={"status_code": 200, "headers": {}, "body": "<html>ok</html>"},
            )

        async with _bd_fetcher(handler) as f:
            html = await f.fetch_html("B08N5WRWNW", "US")
        assert html == "<html>ok</html>"
        assert seen[0]["zone"] == "z"
        assert seen[0]["url"] == "https://amazon.com/dp/B08N5WRWNW"
        assert seen[0]["format"] == "json"

    async def test_sends_bearer_token(self):
        headers_seen: list[str] = []

        def handler(req):
            headers_seen.append(req.headers.get("authorization", ""))
            return httpx.Response(
                200, json={"status_code": 200, "headers": {}, "body": ""}
            )

        async with _bd_fetcher(handler) as f:
            await f.fetch_html("B08N5WRWNW", "US")
        assert headers_seen[0] == "Bearer t"

    async def test_raises_on_upstream_4xx(self):
        # Amazon 404 ("dogs of Amazon") must not reach the parser — BD in raw
        # mode would hide this behind a 200, so format=json is the safeguard.
        def handler(req):
            return httpx.Response(
                200,
                json={"status_code": 404, "headers": {}, "body": "<html>404</html>"},
            )

        async with _bd_fetcher(handler) as f:
            with pytest.raises(FetchError, match="upstream 404"):
                await f.fetch_html("B08N5WRWNW", "US")

    async def test_raises_on_upstream_5xx(self):
        def handler(req):
            return httpx.Response(
                200,
                json={"status_code": 503, "headers": {}, "body": ""},
            )

        async with _bd_fetcher(handler) as f:
            with pytest.raises(FetchError, match="upstream 503"):
                await f.fetch_html("B08N5WRWNW", "US")

    async def test_raises_on_bd_api_error(self):
        def handler(req):
            return httpx.Response(401, text="unauthorized")

        async with _bd_fetcher(handler) as f:
            with pytest.raises(FetchError, match="Bright Data API error 401"):
                await f.fetch_html("B08N5WRWNW", "US")

    async def test_unknown_country_raises(self):
        def handler(req):
            return httpx.Response(200, json={})

        async with _bd_fetcher(handler) as f:
            with pytest.raises(FetchError, match="Unsupported country"):
                await f.fetch_html("B08N5WRWNW", "ZZ")
