"""HTTP fetcher for Amazon product pages.

The ONLY module that talks to the Amazon network. Returns raw HTML strings;
parsing is parser.py's problem. Swapping proxy vendors, rotating user agents,
adding captcha handling — all happens here without touching anything else.
"""

import asyncio
from collections.abc import Callable

import httpx

# Amazon marketplace domains, keyed by ISO 3166-1 alpha-2 country code.
# Add new marketplaces here when you need them.
_COUNTRY_TO_DOMAIN = {
    "US": "amazon.com", "CA": "amazon.ca", "MX": "amazon.com.mx",
    "BR": "amazon.com.br", "GB": "amazon.co.uk", "DE": "amazon.de",
    "FR": "amazon.fr", "IT": "amazon.it", "ES": "amazon.es",
    "NL": "amazon.nl", "SE": "amazon.se", "PL": "amazon.pl",
    "TR": "amazon.com.tr", "JP": "amazon.co.jp", "AU": "amazon.com.au",
    "IN": "amazon.in", "AE": "amazon.ae", "SA": "amazon.sa",
}

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class FetchError(Exception):
    """Raised when a page cannot be fetched (unknown country, 4xx, or retries exhausted)."""


def _default_backoff(attempt: int) -> float:
    # 0.5s, 1s, 2s — exponential, but short enough that a user waiting on a
    # scrape doesn't notice a single retry.
    return 0.5 * (2**attempt)


class AmazonFetcher:
    """Fetches Amazon product-page HTML. One instance reuses one connection pool."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        backoff: Callable[[int], float] = _default_backoff,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            # When a test injects transport, proxy is irrelevant — transport wins.
            proxy=proxy_url if transport is None else None,
            timeout=timeout,
            headers=_DEFAULT_HEADERS,
            transport=transport,
            follow_redirects=True,
        )
        self._max_retries = max_retries
        self._backoff = backoff

    async def fetch_html(self, asin: str, country_code: str) -> str:
        domain = _COUNTRY_TO_DOMAIN.get(country_code.upper())
        if domain is None:
            raise FetchError(f"Unsupported country: {country_code}")
        url = f"https://{domain}/dp/{asin}"
        return await self._get_with_retries(url)

    async def _get_with_retries(self, url: str) -> str:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_exc = exc
            else:
                if 200 <= resp.status_code < 300:
                    return resp.text
                if 400 <= resp.status_code < 500:
                    # Permanent: don't waste budget retrying.
                    raise FetchError(f"client error {resp.status_code} for {url}")
                # 5xx — treat as transient.
                last_exc = FetchError(f"upstream {resp.status_code}")

            if attempt < self._max_retries:
                await asyncio.sleep(self._backoff(attempt))

        raise FetchError(
            f"fetch failed after {self._max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AmazonFetcher":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()
