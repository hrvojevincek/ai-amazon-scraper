"""Tests for AgentTools dispatch.

We don't test the Agent LLM loop here — that's integration territory.
What we CAN test is that every tool name dispatches to the right service
and that results come back as valid JSON strings the model can parse.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from new_amazon_scraper.agent import AgentTools
from new_amazon_scraper.product import Product
from new_amazon_scraper.repo import InMemoryProductRepository
from new_amazon_scraper.search import InMemorySearch

FIXTURES = Path(__file__).parent / "fixtures"


def _product(**overrides) -> Product:
    base = {
        "asin": "B08N5WRWNW",
        "country_code": "US",
        "scraped_at": datetime(2026, 4, 22, tzinfo=UTC),
        "title": "Echo Dot smart speaker",
        "brand": "Amazon",
        "price": Decimal("49.99"),
        "currency": "USD",
    }
    return Product(**(base | overrides))


class FakeFetcher:
    """Returns canned HTML. Same shape as the scraper's HtmlFetcher protocol."""

    def __init__(self, html: str):
        self._html = html

    async def fetch_html(self, asin: str, country_code: str) -> str:
        return self._html


@pytest.fixture
async def tools_with_data():
    repo = InMemoryProductRepository()
    search = InMemorySearch()
    fetcher = FakeFetcher((FIXTURES / "sample_product.html").read_text())
    p = _product()
    await repo.save(p)
    await search.index(p)
    return AgentTools(repo=repo, search=search, fetcher=fetcher)


@pytest.fixture
def tools_empty() -> AgentTools:
    return AgentTools(
        repo=InMemoryProductRepository(),
        search=InMemorySearch(),
    )


class TestSpecs:
    def test_scrape_tool_hidden_when_no_fetcher(self, tools_empty):
        names = [s.name for s in tools_empty.specs()]
        assert "scrape_product" not in names

    async def test_scrape_tool_present_when_fetcher_provided(self, tools_with_data):
        names = [s.name for s in tools_with_data.specs()]
        assert "scrape_product" in names

    def test_every_spec_has_required_fields(self, tools_empty):
        for spec in tools_empty.specs():
            assert spec.name
            assert spec.description
            assert spec.parameters["type"] == "object"


class TestExecute:
    async def test_get_product_hit(self, tools_with_data):
        result = await tools_with_data.execute(
            "get_product", {"asin": "B08N5WRWNW", "country_code": "US"}
        )
        data = json.loads(result)
        assert data["asin"] == "B08N5WRWNW"
        assert data["title"] == "Echo Dot smart speaker"

    async def test_get_product_miss_returns_null(self, tools_with_data):
        result = await tools_with_data.execute(
            "get_product", {"asin": "B00NOTFOUND", "country_code": "US"}
        )
        assert result == "null"

    async def test_search_products_returns_ranked_json(self, tools_with_data):
        result = await tools_with_data.execute(
            "search_products", {"query": "speaker"}
        )
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["asin"] == "B08N5WRWNW"
        assert "score" in data[0]

    async def test_get_price_history_returns_list(self, tools_with_data):
        result = await tools_with_data.execute(
            "get_price_history", {"asin": "B08N5WRWNW", "country_code": "US"}
        )
        data = json.loads(result)
        assert len(data) == 1
        assert data[0]["price"] == "49.99"

    async def test_scrape_product_happy_path(self, tools_with_data):
        # Fixture HTML parses to a valid product → dispatch saves + indexes.
        result = await tools_with_data.execute(
            "scrape_product", {"asin": "B08N5WRWNW", "country_code": "US"}
        )
        data = json.loads(result)
        assert data["asin"] == "B08N5WRWNW"
        assert data["title"]

    async def test_unknown_tool_returns_error(self, tools_with_data):
        result = await tools_with_data.execute("teleport", {})
        assert json.loads(result) == {"error": "unknown tool: teleport"}

    async def test_scrape_unavailable_without_fetcher(self, tools_empty):
        result = await tools_empty.execute(
            "scrape_product", {"asin": "B08N5WRWNW", "country_code": "US"}
        )
        # No fetcher → falls through to the unknown-tool branch.
        assert "error" in json.loads(result)

    async def test_exceptions_become_json_errors(self, tools_empty):
        # Missing required arg — the dispatcher should catch and serialize,
        # not crash the conversation.
        result = await tools_empty.execute("search_products", {})
        assert "error" in json.loads(result)
