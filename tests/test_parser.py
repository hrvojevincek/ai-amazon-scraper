"""Tests for parser.py.

Parser is tested end-to-end through its single public function. When a
behavior is hard to exercise through parse_product(), that's a design
signal, not a reason to reach into private helpers.
"""

from decimal import Decimal
from pathlib import Path

import pytest

from new_amazon_scraper.parser import parse_product

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def sample_html() -> str:
    return (FIXTURES / "sample_product.html").read_text()


class TestFullDocument:
    def test_title_extracted_and_whitespace_stripped(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.title == "Echo Dot (4th Gen) | Smart speaker with Alexa | Charcoal"

    def test_brand_strips_visit_the_prefix_and_store_suffix(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.brand == "Amazon"

    def test_price_is_decimal(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.price == Decimal("49.99")

    def test_currency_derived_from_country(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.currency == "USD"

    def test_rating(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.rating == 4.7

    def test_review_count_strips_commas(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.review_count == 1234

    def test_availability(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.availability == "In Stock"

    def test_canonical_url(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert str(p.product_url).rstrip("/") == "https://www.amazon.com/dp/B08N5WRWNW"

    def test_images_prefer_hires_and_dedupe(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        urls = [str(u) for u in p.images]
        # Hi-res from data-old-hires wins over the _SS40_ thumbnail src.
        assert any("71abc.jpg" in u for u in urls)
        assert not any("_SS40_" in u for u in urls)
        # landingImage and altImages both contribute, no duplicates.
        assert len(urls) == len(set(urls))

    def test_categories_breadcrumb(self, sample_html):
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.categories == ["Electronics", "Smart Home", "Speakers"]

    def test_result_is_valid(self, sample_html):
        # Full document has title → product is valid for persistence.
        p = parse_product(sample_html, "B08N5WRWNW", "US")
        assert p.is_valid() is True


class TestPriceNormalization:
    """Price formats vary by marketplace — this is where parsers most often break."""

    @pytest.mark.parametrize(
        "html, expected",
        [
            ('<span class="a-price"><span class="a-offscreen">$49.99</span></span>',
             Decimal("49.99")),
            ('<span class="a-price"><span class="a-offscreen">$1,299.99</span></span>',
             Decimal("1299.99")),
            ('<span class="a-price"><span class="a-offscreen">29,95 €</span></span>',
             Decimal("29.95")),
            ('<span class="a-price"><span class="a-offscreen">£9.50</span></span>',
             Decimal("9.50")),
            ('<span class="a-price"><span class="a-offscreen">¥1500</span></span>',
             Decimal("1500")),
        ],
    )
    def test_variants(self, html, expected):
        full = f"<html><body><span id='productTitle'>x</span>{html}</body></html>"
        p = parse_product(full, "B08N5WRWNW", "US")
        assert p.price == expected

    def test_no_price_element(self):
        html = "<html><body><span id='productTitle'>x</span></body></html>"
        p = parse_product(html, "B08N5WRWNW", "US")
        assert p.price is None


class TestMinimalAndInvalid:
    def test_empty_html_produces_invalid_product(self):
        p = parse_product("<html></html>", "B08N5WRWNW", "US")
        assert p.title is None
        assert p.price is None
        assert p.is_valid() is False

    def test_country_normalized_lowercase(self):
        # Fetcher might hand us "us" — parser should still produce USD.
        html = "<html><body><span id='productTitle'>x</span></body></html>"
        p = parse_product(html, "B08N5WRWNW", "us")
        assert p.country_code == "US"
        assert p.currency == "USD"

    def test_unknown_country_yields_no_currency(self):
        # Any valid 2-letter code we haven't mapped → currency is None, not a crash.
        html = "<html><body><span id='productTitle'>x</span></body></html>"
        p = parse_product(html, "B08N5WRWNW", "ZZ")
        assert p.country_code == "ZZ"
        assert p.currency is None


class TestRatingBounds:
    def test_rating_above_five_dropped(self):
        # If Amazon markup returns nonsense like "12.3 out of 5", we drop it
        # rather than raise — Product validates to reject, but parser should
        # never let a bad number reach Product and blow up the whole parse.
        html = """<html><body>
          <span id='productTitle'>x</span>
          <span class="a-icon-alt">12.3 out of 5 stars</span>
        </body></html>"""
        p = parse_product(html, "B08N5WRWNW", "US")
        assert p.rating is None
