"""End-to-end tests for PostgresProductRepository against real Postgres.

The unit tests cover the in-memory adapter — those nail the contract.
These tests catch the things only Postgres can break: composite-PK upserts,
the price_history FK cascade, JSONB round-tripping, ordering by updated_at.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import HttpUrl
from sqlalchemy import select, text

from new_amazon_scraper.db import PriceHistoryRow, ProductRow
from new_amazon_scraper.product import Product
from new_amazon_scraper.repo import PostgresProductRepository


def _product(**overrides) -> Product:
    base = {
        "asin": "B08N5WRWNW",
        "country_code": "US",
        "scraped_at": datetime(2026, 4, 22, tzinfo=UTC),
        "title": "Echo Dot smart speaker",
        "brand": "Amazon",
        "price": Decimal("49.99"),
        "currency": "USD",
        "rating": 4.7,
        "review_count": 12345,
        "images": [HttpUrl("https://example.com/echo.jpg")],
        "categories": ["Electronics", "Speakers"],
    }
    return Product(**(base | overrides))


@pytest.fixture
def repo(session_factory) -> PostgresProductRepository:
    return PostgresProductRepository(session_factory)


class TestSaveAndGet:
    async def test_round_trip_full_product(self, repo):
        await repo.save(_product())
        got = await repo.get("B08N5WRWNW", "US")
        assert got is not None
        assert got.title == "Echo Dot smart speaker"
        assert got.price == Decimal("49.99")
        assert got.rating == 4.7
        assert got.categories == ["Electronics", "Speakers"]
        assert [str(u) for u in got.images] == ["https://example.com/echo.jpg"]

    async def test_get_miss_returns_none(self, repo):
        assert await repo.get("B00MISSING", "US") is None

    async def test_save_is_idempotent_for_product_row(self, repo, session_factory):
        # Same composite PK saved twice → one row, latest values win.
        await repo.save(_product(title="old"))
        await repo.save(_product(title="new"))

        got = await repo.get("B08N5WRWNW", "US")
        assert got.title == "new"

        async with session_factory() as session:
            count = await session.scalar(
                select(text("count(*)")).select_from(ProductRow)
            )
        assert count == 1


class TestPriceHistory:
    async def test_each_save_appends_history_row(self, repo, session_factory):
        t0 = datetime(2026, 4, 1, tzinfo=UTC)
        for i in range(3):
            await repo.save(_product(
                price=Decimal(f"{40 + i}.00"),
                scraped_at=t0 + timedelta(days=i),
            ))

        history = await repo.get_price_history("B08N5WRWNW", "US")
        assert len(history) == 3
        # Newest first.
        assert [p.price for p in history] == [Decimal("42.00"), Decimal("41.00"), Decimal("40.00")]

    async def test_save_without_price_skips_history(self, repo):
        await repo.save(_product(price=None))
        assert await repo.get_price_history("B08N5WRWNW", "US") == []

    async def test_history_isolated_per_product(self, repo):
        await repo.save(_product(asin="B0000000AA", price=Decimal("10")))
        await repo.save(_product(asin="B0000000BB", price=Decimal("20")))

        a = await repo.get_price_history("B0000000AA", "US")
        b = await repo.get_price_history("B0000000BB", "US")
        assert [p.price for p in a] == [Decimal("10")]
        assert [p.price for p in b] == [Decimal("20")]

    async def test_history_respects_limit(self, repo):
        t0 = datetime(2026, 4, 1, tzinfo=UTC)
        for i in range(5):
            await repo.save(_product(
                price=Decimal(f"{i}"),
                scraped_at=t0 + timedelta(days=i),
            ))
        history = await repo.get_price_history("B08N5WRWNW", "US", limit=2)
        assert len(history) == 2

    async def test_cascade_delete_removes_history(self, repo, session_factory):
        await repo.save(_product())
        await repo.save(_product(price=Decimal("39.99"),
                                 scraped_at=datetime(2026, 4, 23, tzinfo=UTC)))

        async with session_factory() as session:
            row = await session.get(ProductRow, ("B08N5WRWNW", "US"))
            await session.delete(row)
            await session.commit()

        assert await repo.get("B08N5WRWNW", "US") is None
        async with session_factory() as session:
            remaining = await session.scalar(
                select(text("count(*)")).select_from(PriceHistoryRow)
            )
        assert remaining == 0


class TestListAll:
    async def test_orders_by_updated_at_desc(self, repo):
        # Save A first, then B — B is newer, so list_all returns B first.
        await repo.save(_product(asin="B0000000AA", title="alpha"))
        await repo.save(_product(asin="B0000000BB", title="bravo"))

        products = await repo.list_all()
        assert [p.asin for p in products] == ["B0000000BB", "B0000000AA"]

    async def test_respects_limit(self, repo):
        for i in range(5):
            await repo.save(_product(asin=f"B000000{i}{i}{i}"))
        assert len(await repo.list_all(limit=3)) == 3
