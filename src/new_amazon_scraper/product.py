"""Product domain model.

Pure Python — no DB, no network, no framework coupling.
Every other module in the app depends on this one; this module depends on nothing.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, HttpUrl, StringConstraints


def _upper(v: object) -> object:
    """Coerce strings to upper-case BEFORE pattern validation.

    StringConstraints.to_upper is applied after the pattern check, which is too
    late for case-insensitive inputs like "us" or "eur". A BeforeValidator runs
    first, so the pattern only ever sees upper-case.
    """
    return v.upper() if isinstance(v, str) else v


# --- Type primitives -----------------------------------------------------------
# Amazon ASINs are 10-char uppercase alphanumeric. Strict: reject lowercase.
Asin = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{10}$")]

# ISO 3166-1 alpha-2, e.g. "US", "DE", "JP". Input case-insensitive.
CountryCode = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^[A-Z]{2}$")]

# ISO 4217, e.g. "USD", "EUR", "JPY". Input case-insensitive.
CurrencyCode = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^[A-Z]{3}$")]


class Product(BaseModel):
    """A single Amazon product observation at a point in time.

    Prices live in Decimal because floats corrupt money arithmetic.
    Optional fields are optional because scraping is lossy — the parser returns
    what it found. `is_valid()` tells you whether it's worth persisting.
    """

    asin: Asin
    country_code: CountryCode
    scraped_at: datetime

    title: str | None = None
    brand: str | None = None
    price: Decimal | None = None
    currency: CurrencyCode | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    availability: str | None = None
    product_url: HttpUrl | None = None
    images: list[HttpUrl] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)

    def is_valid(self) -> bool:
        """Business rule: a product is worth keeping if it has a title,
        or both a price and a brand. Anything less is likely a parse failure.
        """
        if self.title:
            return True
        return self.price is not None and bool(self.brand)


class PricePoint(BaseModel):
    """One historical observation of a product's price and stats.

    Used for charting price over time. Always carries a real price — we don't
    record "no price found" points; that would pollute the history.
    """

    price: Decimal
    currency: CurrencyCode | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    scraped_at: datetime
