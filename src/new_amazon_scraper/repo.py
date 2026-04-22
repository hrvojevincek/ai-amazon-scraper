"""Product repository: the seam between domain and persistence.

The `ProductRepository` Protocol defines what the rest of the app needs.
`InMemoryProductRepository` is for tests and local demos.
`PostgresProductRepository` is for real deployments.

Business rules (e.g. "should we save invalid products?") live elsewhere.
The repo persists what it's given.
"""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db import PriceHistoryRow, ProductRow
from .product import PricePoint, Product

# --- Port --------------------------------------------------------------------

class ProductRepository(Protocol):
    async def save(self, product: Product) -> None: ...
    async def get(self, asin: str, country_code: str) -> Product | None: ...
    async def list_all(self, limit: int = 100) -> list[Product]: ...
    async def get_price_history(
        self, asin: str, country_code: str, limit: int = 50
    ) -> list[PricePoint]: ...


# --- Adapter: in-memory (tests, local demos) ---------------------------------

class InMemoryProductRepository:
    """Dict + list backed. Not thread-safe. Not for production."""

    def __init__(self) -> None:
        self._products: dict[tuple[str, str], Product] = {}
        self._history: list[tuple[str, str, PricePoint]] = []

    async def save(self, product: Product) -> None:
        key = (product.asin, product.country_code)
        self._products[key] = product
        if product.price is not None:
            self._history.append((
                product.asin,
                product.country_code,
                PricePoint(
                    price=product.price,
                    currency=product.currency,
                    rating=product.rating,
                    review_count=product.review_count,
                    scraped_at=product.scraped_at,
                ),
            ))

    async def get(self, asin: str, country_code: str) -> Product | None:
        return self._products.get((asin.upper(), country_code.upper()))

    async def list_all(self, limit: int = 100) -> list[Product]:
        return list(self._products.values())[:limit]

    async def get_price_history(
        self, asin: str, country_code: str, limit: int = 50
    ) -> list[PricePoint]:
        key = (asin.upper(), country_code.upper())
        points = [p for a, c, p in self._history if (a, c) == key]
        points.sort(key=lambda p: p.scraped_at, reverse=True)
        return points[:limit]


# --- Adapter: Postgres (production) ------------------------------------------

class PostgresProductRepository:
    """Async SQLAlchemy implementation. One session per operation."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save(self, product: Product) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ProductRow, (product.asin, product.country_code))
            if existing is None:
                session.add(ProductRow.from_product(product))
            else:
                existing.update_from_product(product)

            if product.price is not None:
                session.add(PriceHistoryRow(
                    asin=product.asin,
                    country_code=product.country_code,
                    price=product.price,
                    currency=product.currency,
                    rating=product.rating,
                    review_count=product.review_count,
                    scraped_at=product.scraped_at,
                ))
            await session.commit()

    async def get(self, asin: str, country_code: str) -> Product | None:
        async with self._session_factory() as session:
            row = await session.get(ProductRow, (asin.upper(), country_code.upper()))
            return row.to_product() if row else None

    async def list_all(self, limit: int = 100) -> list[Product]:
        async with self._session_factory() as session:
            stmt = (
                select(ProductRow)
                .order_by(ProductRow.updated_at.desc())
                .limit(limit)
            )
            rows = (await session.scalars(stmt)).all()
            return [r.to_product() for r in rows]

    async def get_price_history(
        self, asin: str, country_code: str, limit: int = 50
    ) -> list[PricePoint]:
        async with self._session_factory() as session:
            stmt = (
                select(PriceHistoryRow)
                .where(PriceHistoryRow.asin == asin.upper())
                .where(PriceHistoryRow.country_code == country_code.upper())
                .order_by(PriceHistoryRow.scraped_at.desc())
                .limit(limit)
            )
            rows = (await session.scalars(stmt)).all()
            return [
                PricePoint(
                    price=r.price,
                    currency=r.currency,
                    rating=r.rating,
                    review_count=r.review_count,
                    scraped_at=r.scraped_at,
                )
                for r in rows
            ]


