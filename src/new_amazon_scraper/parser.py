"""Amazon product-page HTML parser.

Pure function of (html, asin, country_code) → Product. No network, no DB.
Saved HTML fixtures drive the tests; when Amazon changes markup, save a new
fixture, watch the test fail, fix the selector.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag

from .product import Product

# Amazon marketplace → currency (ISO 4217). Country code from the URL the
# fetcher used is unambiguous; symbols like "$" are not (USD/CAD/AUD/MXN).
_COUNTRY_TO_CURRENCY = {
    "US": "USD", "CA": "CAD", "MX": "MXN", "BR": "BRL",
    "GB": "GBP", "DE": "EUR", "FR": "EUR", "IT": "EUR",
    "ES": "EUR", "NL": "EUR", "SE": "SEK", "PL": "PLN",
    "TR": "TRY", "JP": "JPY", "AU": "AUD", "IN": "INR",
    "AE": "AED", "SA": "SAR",
}

_PRICE_NUMBER_RE = re.compile(r"[\d][\d.,]*")
_RATING_RE = re.compile(r"(\d+(?:\.\d+)?)")
_COUNT_RE = re.compile(r"([\d,]+)")


def parse_product(html: str, asin: str, country_code: str) -> Product:
    """Parse an Amazon product page. Returns a Product that may or may not be valid.

    The caller decides whether to persist it (see Product.is_valid()).
    """
    soup = BeautifulSoup(html, "lxml")
    return Product(
        asin=asin,
        country_code=country_code,
        scraped_at=datetime.now(UTC),
        title=_text(soup.select_one("#productTitle")),
        brand=_extract_brand(soup),
        price=_extract_price(soup),
        currency=_COUNTRY_TO_CURRENCY.get(country_code.upper()),
        rating=_extract_rating(soup),
        review_count=_extract_review_count(soup),
        availability=_text(soup.select_one("#availability span")),
        product_url=_extract_canonical_url(soup),
        images=_extract_images(soup),
        categories=_extract_categories(soup),
    )


# --- extractors ---------------------------------------------------------------

def _text(el: Tag | None) -> str | None:
    if el is None:
        return None
    txt = el.get_text(strip=True)
    return txt or None


def _extract_brand(soup: BeautifulSoup) -> str | None:
    el = soup.select_one("#bylineInfo")
    if el is None:
        return None
    txt = el.get_text(strip=True)
    for prefix in ("Visit the ", "Brand: "):
        if txt.startswith(prefix):
            txt = txt[len(prefix):]
    txt = txt.removesuffix(" Store").strip()
    return txt or None


def _extract_price(soup: BeautifulSoup) -> Decimal | None:
    # Try selectors in order — Amazon rotates which holds the active price.
    for selector in (
        "span.a-price span.a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "span.a-price",
    ):
        el = soup.select_one(selector)
        if el is None:
            continue
        normalized = _normalize_price_number(el.get_text())
        if normalized is not None:
            return normalized
    return None


def _normalize_price_number(text: str) -> Decimal | None:
    """Extract and normalize a price number from a string like '$1,299.99' or '29,95 €'."""
    match = _PRICE_NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    # "29,95" → European decimal; "1,299.99" → US thousands separator.
    is_european_decimal = raw.count(",") == 1 and "." not in raw
    raw = raw.replace(",", ".") if is_european_decimal else raw.replace(",", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _extract_rating(soup: BeautifulSoup) -> float | None:
    el = soup.select_one("span.a-icon-alt")
    if el is None:
        return None
    match = _RATING_RE.search(el.get_text())
    if not match:
        return None
    value = float(match.group(1))
    return value if 0 <= value <= 5 else None


def _extract_review_count(soup: BeautifulSoup) -> int | None:
    el = soup.select_one("#acrCustomerReviewText")
    if el is None:
        return None
    match = _COUNT_RE.search(el.get_text())
    return int(match.group(1).replace(",", "")) if match else None


def _extract_canonical_url(soup: BeautifulSoup) -> str | None:
    el = soup.select_one('link[rel="canonical"]')
    href = el.get("href") if el else None
    return href if isinstance(href, str) and href.startswith("http") else None


def _extract_images(soup: BeautifulSoup) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for img in soup.select("#landingImage, #altImages img"):
        src = img.get("data-old-hires") or img.get("src")
        if isinstance(src, str) and src.startswith("http") and src not in seen:
            seen.add(src)
            urls.append(src)
    return urls


def _extract_categories(soup: BeautifulSoup) -> list[str]:
    return [
        a.get_text(strip=True)
        for a in soup.select("#wayfinding-breadcrumbs_feature_div a")
        if a.get_text(strip=True)
    ]
