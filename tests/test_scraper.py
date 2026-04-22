"""Tests for the scraper orchestrator.

Uses a FakeFetcher — a real implementation of the HtmlFetcher protocol,
just an in-memory one. No mock library, no patching.
"""

from pathlib import Path

from new_amazon_scraper.scraper import scrape_product

FIXTURES = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Returns a canned HTML string and records what was asked for."""

    def __init__(self, html: str):
        self._html = html
        self.calls: list[tuple[str, str]] = []

    async def fetch_html(self, asin: str, country_code: str) -> str:
        self.calls.append((asin, country_code))
        return self._html


async def test_scrape_fetches_then_parses():
    html = (FIXTURES / "sample_product.html").read_text()
    fake = FakeFetcher(html)

    product = await scrape_product(fake, "B08N5WRWNW", "US")

    assert product.asin == "B08N5WRWNW"
    assert product.title is not None
    assert product.price is not None
    assert product.is_valid() is True


async def test_scrape_passes_asin_and_country_to_fetcher():
    fake = FakeFetcher("<html><body><span id='productTitle'>x</span></body></html>")

    await scrape_product(fake, "B08N5WRWNW", "DE")

    assert fake.calls == [("B08N5WRWNW", "DE")]


async def test_scrape_propagates_country_to_product():
    # Currency is derived from country_code inside the parser — this test
    # pins down the whole chain: scraper → parser → Product fields.
    fake = FakeFetcher("<html><body><span id='productTitle'>x</span></body></html>")

    product = await scrape_product(fake, "B08N5WRWNW", "DE")

    assert product.country_code == "DE"
    assert product.currency == "EUR"
