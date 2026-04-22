"""Database engine, session factory, and SQLAlchemy ORM models.

Separated from `repo.py` so the Protocol/fake can live without pulling in
SQLAlchemy. Only the Postgres implementation imports from this module.
"""

from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKeyConstraint, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

from .product import Product

EMBEDDING_DIMS = 1536  # OpenAI text-embedding-3-small


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


class ProductRow(Base):
    """`products` — latest observed state for (asin, country_code)."""

    __tablename__ = "products"

    asin: Mapped[str] = mapped_column(String(10), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), primary_key=True)

    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    rating: Mapped[float | None] = mapped_column(nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    availability: Mapped[str | None] = mapped_column(Text, nullable=True)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    images: Mapped[list[str]] = mapped_column(JSONB, default=list)
    categories: Mapped[list[str]] = mapped_column(JSONB, default=list)

    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Nullable: rows exist before Search indexes them. Search backfills later.
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMS), nullable=True
    )

    # --- Mapping helpers. Domain ↔ ORM lives on the row itself so every
    # module that hydrates Products from rows does it the same way.

    @classmethod
    def from_product(cls, p: Product) -> "ProductRow":
        return cls(
            asin=p.asin,
            country_code=p.country_code,
            title=p.title,
            brand=p.brand,
            price=p.price,
            currency=p.currency,
            rating=p.rating,
            review_count=p.review_count,
            availability=p.availability,
            product_url=str(p.product_url) if p.product_url else None,
            images=[str(u) for u in p.images],
            categories=list(p.categories),
            scraped_at=p.scraped_at,
        )

    def update_from_product(self, p: Product) -> None:
        self.title = p.title
        self.brand = p.brand
        self.price = p.price
        self.currency = p.currency
        self.rating = p.rating
        self.review_count = p.review_count
        self.availability = p.availability
        self.product_url = str(p.product_url) if p.product_url else None
        self.images = [str(u) for u in p.images]
        self.categories = list(p.categories)
        self.scraped_at = p.scraped_at

    def to_product(self) -> Product:
        return Product(
            asin=self.asin,
            country_code=self.country_code,
            title=self.title,
            brand=self.brand,
            price=self.price,
            currency=self.currency,
            rating=self.rating,
            review_count=self.review_count,
            availability=self.availability,
            product_url=self.product_url,
            images=self.images,
            categories=self.categories,
            scraped_at=self.scraped_at,
        )


class PriceHistoryRow(Base):
    """`price_history` — append-only log, one row per successful scrape with a price."""

    __tablename__ = "price_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asin", "country_code"],
            ["products.asin", "products.country_code"],
            ondelete="CASCADE",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asin: Mapped[str] = mapped_column(String(10))
    country_code: Mapped[str] = mapped_column(String(2))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    rating: Mapped[float | None] = mapped_column(nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def create_engine(database_url: str) -> AsyncEngine:
    """Build an async engine. `pool_pre_ping` catches stale connections (e.g. after
    Postgres restart) without crashing the request that happens to reuse one.
    """
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """`expire_on_commit=False` lets callers read attributes off ORM objects
    after commit without triggering lazy-load round-trips.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
