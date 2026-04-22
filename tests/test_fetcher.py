"""Tests for fetcher.py.

httpx.MockTransport replaces the network. Retries use a zero-delay backoff
so tests run instantly. Every behavior (URL building, retry, client-vs-server
errors, exhaustion) is exercised without hitting anything real.
"""

import httpx
import pytest

from new_amazon_scraper.fetcher import AmazonFetcher, FetchError


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
