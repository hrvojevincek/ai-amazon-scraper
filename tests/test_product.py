"""Tests for the Product domain model.

These run without any infrastructure — no DB, no network, no env vars.
If these ever need a fixture or a mock, something is wrong with the module.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from new_amazon_scraper.product import Product


def _base(**overrides) -> dict:
    """Minimal valid kwargs for Product. Tests override one field at a time."""
    return {
        "asin": "B08N5WRWNW",
        "country_code": "US",
        "scraped_at": datetime(2026, 4, 22, tzinfo=UTC),
    } | overrides


class TestAsin:
    def test_accepts_valid(self):
        p = Product(**_base(), title="Echo Dot")
        assert p.asin == "B08N5WRWNW"

    def test_rejects_too_short(self):
        with pytest.raises(ValidationError):
            Product(**_base(asin="B08"))

    def test_rejects_lowercase(self):
        # Be strict: Amazon's canonical form is uppercase. Fail loud, don't silently coerce.
        with pytest.raises(ValidationError):
            Product(**_base(asin="b08n5wrwnw"))


class TestCountryCurrency:
    def test_country_code_upper_cased(self):
        p = Product(**_base(country_code="us"), title="x")
        assert p.country_code == "US"

    def test_currency_upper_cased(self):
        p = Product(**_base(), title="x", currency="eur", price=Decimal("9.99"), brand="Acme")
        assert p.currency == "EUR"

    def test_invalid_country_rejected(self):
        with pytest.raises(ValidationError):
            Product(**_base(country_code="USA"))


class TestPrice:
    def test_float_coerces_to_decimal(self):
        p = Product(**_base(), title="x", price=19.99)
        assert isinstance(p.price, Decimal)

    def test_string_coerces_to_decimal(self):
        p = Product(**_base(), title="x", price="19.99")
        assert p.price == Decimal("19.99")


class TestRating:
    def test_in_range(self):
        p = Product(**_base(), title="x", rating=4.3)
        assert p.rating == 4.3

    def test_above_five_rejected(self):
        with pytest.raises(ValidationError):
            Product(**_base(), title="x", rating=6.0)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            Product(**_base(), title="x", rating=-1.0)


class TestIsValid:
    def test_title_alone_is_valid(self):
        assert Product(**_base(), title="Echo Dot").is_valid() is True

    def test_price_plus_brand_is_valid(self):
        p = Product(**_base(), brand="Amazon", price=Decimal("49.99"))
        assert p.is_valid() is True

    def test_price_without_brand_is_invalid(self):
        p = Product(**_base(), price=Decimal("49.99"))
        assert p.is_valid() is False

    def test_brand_without_price_is_invalid(self):
        p = Product(**_base(), brand="Amazon")
        assert p.is_valid() is False

    def test_empty_is_invalid(self):
        assert Product(**_base()).is_valid() is False
