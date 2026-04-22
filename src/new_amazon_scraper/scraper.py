"""Scraper — composes fetcher + parser into a single product-scrape workflow.

Depends on the `HtmlFetcher` protocol, not any specific fetcher. Swap in a
caching fetcher, a retrying fetcher, or a test fake without touching this file.
"""

from typing import Protocol

from .parser import parse_product
from .product import Product


class HtmlFetcher(Protocol):
    """Anything that can give us Amazon HTML for a given ASIN + country."""

    async def fetch_html(self, asin: str, country_code: str) -> str: ...


async def scrape_product(
    fetcher: HtmlFetcher, asin: str, country_code: str
) -> Product:
    """Fetch one Amazon product page and parse it into a Product."""
    html = await fetcher.fetch_html(asin, country_code)
    return parse_product(html, asin, country_code)
