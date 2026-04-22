"""End-to-end tests for OpenAIPgVectorSearch against pgvector.

We stub the OpenAI client — those calls cost money and we're not testing
OpenAI here, we're testing that:
  1. embeddings round-trip through the Vector column,
  2. cosine_distance ranks results the way we expect,
  3. only embedded rows show up in results,
  4. the search hits the existing product row (no orphaned writes).
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from new_amazon_scraper.product import Product
from new_amazon_scraper.repo import PostgresProductRepository
from new_amazon_scraper.search import OpenAIPgVectorSearch, _embed_text

EMBEDDING_DIMS = 1536


def _vec(*nonzero_dims: int) -> list[float]:
    """Build a 1536-dim unit-ish vector with given dims set. Easy to reason
    about cosine distance: orthogonal vectors share no nonzero dims."""
    v = [0.0] * EMBEDDING_DIMS
    for d in nonzero_dims:
        v[d] = 1.0
    return v


class StubEmbeddings:
    def __init__(self, vectors_by_text: dict[str, list[float]]):
        self._vectors = vectors_by_text

    async def create(self, *, model: str, input: str):  # noqa: A002 - matches OpenAI API
        if input not in self._vectors:
            raise KeyError(f"stub has no vector for {input!r}")
        return SimpleNamespace(data=[SimpleNamespace(embedding=self._vectors[input])])


class StubOpenAI:
    def __init__(self, vectors_by_text: dict[str, list[float]]):
        self.embeddings = StubEmbeddings(vectors_by_text)


def _product(**overrides) -> Product:
    base = {
        "asin": "B0000000AA",
        "country_code": "US",
        "scraped_at": datetime(2026, 4, 22, tzinfo=UTC),
        "title": "Echo Dot smart speaker",
        "brand": "Amazon",
        "price": Decimal("49.99"),
        "currency": "USD",
    }
    return Product(**(base | overrides))


@pytest.fixture
def repo(session_factory) -> PostgresProductRepository:
    return PostgresProductRepository(session_factory)


class TestIndexAndQuery:
    async def test_query_ranks_closer_vector_first(self, session_factory, repo):
        echo = _product(asin="B0000000AA", title="Echo Dot")
        sony = _product(asin="B0000000BB", title="Sony WH-1000XM5", brand="Sony")
        # Echo Dot uses dims 0,1; Sony uses dims 2,3 → orthogonal.
        # Query "speaker" overlaps with Echo's vector.
        vectors = {
            _embed_text(echo): _vec(0, 1),
            _embed_text(sony): _vec(2, 3),
            "speaker": _vec(0, 1),
        }
        client = StubOpenAI(vectors)
        search = OpenAIPgVectorSearch(
            session_factory=session_factory, openai_client=client
        )

        await repo.save(echo)
        await repo.save(sony)
        await search.index(echo)
        await search.index(sony)

        hits = await search.query("speaker", limit=10)

        assert [h.product.asin for h in hits] == ["B0000000AA", "B0000000BB"]
        assert hits[0].score > hits[1].score
        assert 0.0 <= hits[1].score <= 1.0

    async def test_only_indexed_products_returned(self, session_factory, repo):
        # Two products saved, only one indexed → query returns only the indexed one.
        a = _product(asin="B0000000AA")
        b = _product(asin="B0000000BB")
        vectors = {_embed_text(a): _vec(0, 1), "anything": _vec(0, 1)}
        client = StubOpenAI(vectors)
        search = OpenAIPgVectorSearch(
            session_factory=session_factory, openai_client=client
        )

        await repo.save(a)
        await repo.save(b)
        await search.index(a)  # b deliberately not indexed

        hits = await search.query("anything")
        assert [h.product.asin for h in hits] == ["B0000000AA"]

    async def test_index_without_existing_row_raises(self, session_factory):
        p = _product()
        client = StubOpenAI({_embed_text(p): _vec(0)})
        search = OpenAIPgVectorSearch(
            session_factory=session_factory, openai_client=client
        )
        # Repo never saw it → search.index has no row to attach to.
        with pytest.raises(ValueError, match="not found"):
            await search.index(p)

    async def test_query_respects_limit(self, session_factory, repo):
        prods = [_product(asin=f"B000000{i}AA") for i in range(5)]
        vectors = {_embed_text(p): _vec(i) for i, p in enumerate(prods)}
        vectors["q"] = _vec(0)  # match prods[0] best
        client = StubOpenAI(vectors)
        search = OpenAIPgVectorSearch(
            session_factory=session_factory, openai_client=client
        )

        for p in prods:
            await repo.save(p)
            await search.index(p)

        hits = await search.query("q", limit=2)
        assert len(hits) == 2
