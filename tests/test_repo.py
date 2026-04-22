"""Tests for the repository Protocol, exercised against the in-memory adapter.

The real Postgres adapter is covered by integration tests in Step 11.
These tests pin down the *contract* — what every repo implementation must do.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from new_amazon_scraper.product import Product
from new_amazon_scraper.repo import InMemoryProductRepository


def _product(**overrides) -> Product:
    base = {
        "asin": "B08N5WRWNW",
        "country_code": "US",
        "scraped_at": datetime(2026, 4, 22, tzinfo=UTC),
        "title": "Echo Dot",
    }
    return Product(**(base | overrides))


@pytest.fixture
def repo() -> InMemoryProductRepository:
    return InMemoryProductRepository()


class TestSaveAndGet:
    async def test_save_then_get_roundtrip(self, repo):
        await repo.save(_product(title="Echo Dot"))

        got = await repo.get("B08N5WRWNW", "US")

        assert got is not None
        assert got.title == "Echo Dot"

    async def test_get_returns_none_for_missing(self, repo):
        assert await repo.get("B08N5WRWNW", "US") is None

    async def test_get_is_case_insensitive(self, repo):
        await repo.save(_product())

        assert await repo.get("b08n5wrwnw", "us") is not None

    async def test_save_updates_existing(self, repo):
        await repo.save(_product(title="Old"))
        await repo.save(_product(title="New"))

        got = await repo.get("B08N5WRWNW", "US")
        assert got is not None
        assert got.title == "New"


class TestPriceHistory:
    async def test_each_save_appends_history_if_price_present(self, repo):
        t1 = datetime(2026, 4, 22, 10, 0, tzinfo=UTC)
        t2 = datetime(2026, 4, 22, 11, 0, tzinfo=UTC)

        await repo.save(_product(price=Decimal("49.99"), scraped_at=t1))
        await repo.save(_product(price=Decimal("44.99"), scraped_at=t2))

        history = await repo.get_price_history("B08N5WRWNW", "US")
        assert [p.price for p in history] == [Decimal("44.99"), Decimal("49.99")]

    async def test_history_is_newest_first(self, repo):
        t1 = datetime(2026, 4, 20, tzinfo=UTC)
        t2 = datetime(2026, 4, 22, tzinfo=UTC)

        await repo.save(_product(price=Decimal("10"), scraped_at=t1))
        await repo.save(_product(price=Decimal("20"), scraped_at=t2))

        history = await repo.get_price_history("B08N5WRWNW", "US")
        assert history[0].scraped_at > history[1].scraped_at

    async def test_no_price_means_no_history_point(self, repo):
        await repo.save(_product(title="x"))  # no price
        assert await repo.get_price_history("B08N5WRWNW", "US") == []

    async def test_history_scoped_to_asin_and_country(self, repo):
        await repo.save(_product(price=Decimal("10")))  # US
        await repo.save(_product(country_code="DE", price=Decimal("11"), currency="EUR"))

        us_history = await repo.get_price_history("B08N5WRWNW", "US")
        de_history = await repo.get_price_history("B08N5WRWNW", "DE")

        assert len(us_history) == 1
        assert len(de_history) == 1
        assert us_history[0].price == Decimal("10")
        assert de_history[0].price == Decimal("11")

    async def test_history_respects_limit(self, repo):
        base = datetime(2026, 4, 22, tzinfo=UTC)
        for i in range(5):
            await repo.save(_product(price=Decimal(i), scraped_at=base + timedelta(hours=i)))

        history = await repo.get_price_history("B08N5WRWNW", "US", limit=3)
        assert len(history) == 3


class TestListAll:
    async def test_empty(self, repo):
        assert await repo.list_all() == []

    async def test_returns_saved_products(self, repo):
        await repo.save(_product(asin="B08N5WRWNW"))
        await repo.save(_product(asin="B09ABCDEFG"))

        products = await repo.list_all()
        assert {p.asin for p in products} == {"B08N5WRWNW", "B09ABCDEFG"}

    async def test_respects_limit(self, repo):
        for i in range(5):
            await repo.save(_product(asin=f"B0{i}{i}{i}{i}{i}{i}{i}{i}"))
        assert len(await repo.list_all(limit=2)) == 2
