"""Semantic search over products.

Owns embedding generation, storage, and similarity queries. Callers see two
methods — `index(product)` after saving, `query(text, limit)` to search.
They never know about OpenAI, pgvector, or cosine distance.
"""

from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .db import ProductRow
from .product import Product


@dataclass(frozen=True)
class SearchHit:
    """A product returned from search, with a 0-1 similarity score."""

    product: Product
    score: float  # 1.0 = identical, 0.0 = orthogonal


# --- Port --------------------------------------------------------------------

class Search(Protocol):
    async def index(self, product: Product) -> None: ...
    async def query(self, text: str, limit: int = 10) -> list[SearchHit]: ...


def _embed_text(product: Product) -> str:
    """The text we embed for a product.

    Title carries most of the semantic signal; brand + categories add context.
    Price/rating/review_count change often — embedding them would mean
    re-embedding on every scrape, for no retrieval benefit.
    """
    parts: list[str] = []
    if product.title:
        parts.append(product.title)
    if product.brand:
        parts.append(f"Brand: {product.brand}")
    if product.categories:
        parts.append(f"Categories: {' > '.join(product.categories)}")
    return "\n".join(parts) or product.asin  # OpenAI rejects empty input


# --- Adapter: in-memory (tests, local demos) ---------------------------------

class InMemorySearch:
    """Substring-match fake. No real embeddings, no OpenAI. Good enough for contract tests."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[Product, str]] = {}

    async def index(self, product: Product) -> None:
        key = (product.asin, product.country_code)
        self._entries[key] = (product, _embed_text(product).lower())

    async def query(self, text: str, limit: int = 10) -> list[SearchHit]:
        needle = text.lower().strip()
        hits = [
            SearchHit(product=p, score=_word_overlap(needle, indexed))
            for p, indexed in self._entries.values()
        ]
        hits = [h for h in hits if h.score > 0]
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]


def _word_overlap(query: str, indexed: str) -> float:
    """Fraction of query words that appear anywhere in the indexed text."""
    words = [w for w in query.split() if w]
    if not words:
        return 0.0
    matches = sum(1 for w in words if w in indexed)
    return matches / len(words)


# --- Adapter: OpenAI + pgvector (production) ---------------------------------

class OpenAIPgVectorSearch:
    """Real implementation. Embeds with OpenAI, queries via pgvector cosine distance."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        openai_client: AsyncOpenAI,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        self._sf = session_factory
        self._client = openai_client
        self._model = embedding_model

    async def index(self, product: Product) -> None:
        """Embed the product's text and attach the vector to its existing row.

        Raises ValueError if the row doesn't exist — call repo.save() first.
        """
        text = _embed_text(product)
        vector = await self._embed(text)
        async with self._sf() as session:
            row = await session.get(ProductRow, (product.asin, product.country_code))
            if row is None:
                raise ValueError(
                    f"Product {product.asin}/{product.country_code} not found — "
                    "save via repo before indexing"
                )
            row.embedding = vector
            await session.commit()

    async def query(self, text: str, limit: int = 10) -> list[SearchHit]:
        vector = await self._embed(text)
        async with self._sf() as session:
            distance = ProductRow.embedding.cosine_distance(vector)
            stmt = (
                select(ProductRow, distance.label("distance"))
                .where(ProductRow.embedding.is_not(None))
                .order_by(distance)
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()
            # cosine_distance ∈ [0, 2]; 0 = identical. Convert to a 0-1 similarity.
            return [
                SearchHit(product=row.to_product(), score=max(0.0, 1.0 - float(dist)))
                for row, dist in rows
            ]

    async def _embed(self, text: str) -> list[float]:
        resp = await self._client.embeddings.create(model=self._model, input=text)
        return resp.data[0].embedding
