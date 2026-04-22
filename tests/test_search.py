"""Tests for the Search contract, exercised against the in-memory adapter.

The OpenAI+pgvector implementation is covered by integration tests in Step 11.
"""

from datetime import UTC, datetime

import pytest

from new_amazon_scraper.product import Product
from new_amazon_scraper.search import InMemorySearch, _embed_text


def _product(**overrides) -> Product:
    base = {
        "asin": "B08N5WRWNW",
        "country_code": "US",
        "scraped_at": datetime(2026, 4, 22, tzinfo=UTC),
        "title": "Echo Dot smart speaker",
    }
    return Product(**(base | overrides))


@pytest.fixture
def search() -> InMemorySearch:
    return InMemorySearch()


class TestEmbedText:
    def test_title_only(self):
        p = _product(title="Widget")
        assert _embed_text(p) == "Widget"

    def test_title_and_brand_and_categories(self):
        p = _product(title="Widget", brand="Acme", categories=["A", "B"])
        assert _embed_text(p) == "Widget\nBrand: Acme\nCategories: A > B"

    def test_falls_back_to_asin_when_no_semantic_fields(self):
        # Never return empty — OpenAI rejects it.
        p = _product(title=None)
        assert _embed_text(p) == p.asin


class TestIndexAndQuery:
    async def test_query_finds_indexed_product_by_title_word(self, search):
        await search.index(_product(asin="B00000000A", title="Echo Dot speaker"))

        hits = await search.query("speaker")

        assert len(hits) == 1
        assert hits[0].product.asin == "B00000000A"
        assert hits[0].score > 0

    async def test_query_returns_empty_when_nothing_matches(self, search):
        await search.index(_product(title="blender"))

        hits = await search.query("submarine")

        assert hits == []

    async def test_query_ranks_by_word_overlap(self, search):
        await search.index(_product(asin="B00000000A", title="Echo Dot"))
        await search.index(_product(asin="B00000000B", title="Echo Studio smart speaker"))

        hits = await search.query("echo speaker")

        # "Echo Studio smart speaker" hits both query words.
        # "Echo Dot" hits only "echo".
        assert hits[0].product.asin == "B00000000B"
        assert hits[0].score > hits[1].score

    async def test_re_indexing_updates_in_place(self, search):
        await search.index(_product(title="old"))
        await search.index(_product(title="new"))

        hits = await search.query("new")
        assert len(hits) == 1
        assert hits[0].product.title == "new"

    async def test_query_respects_limit(self, search):
        for i, word in enumerate(["apple", "apricot", "avocado", "aubergine"]):
            await search.index(_product(asin=f"B0000000{i}{i}", title=f"{word} thing"))

        hits = await search.query("thing", limit=2)
        assert len(hits) == 2

    async def test_brand_is_searchable(self, search):
        await search.index(_product(title="mystery item", brand="Sony"))

        hits = await search.query("sony")
        assert len(hits) == 1

    async def test_categories_are_searchable(self, search):
        await search.index(_product(title="x", brand="y", categories=["Kitchen", "Knives"]))

        hits = await search.query("knives")
        assert len(hits) == 1
