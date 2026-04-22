"""Tests for the HTTP API.

Composition is tested via `create_app(...)` with in-memory adapters and a fake
fetcher/agent. The lifespan-based `create_production_app()` is exercised by the
integration suite — wiring real Postgres + OpenAI is not a unit-test concern.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from new_amazon_scraper.api import create_app
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
    def __init__(self, html: str):
        self._html = html

    async def fetch_html(self, asin: str, country_code: str) -> str:
        return self._html


class FakeAgent:
    """Duck-types Agent — only `.ask()` is exercised by the endpoint."""

    def __init__(self, answer: str):
        self._answer = answer
        self.questions: list[str] = []

    async def ask(self, question: str) -> str:
        self.questions.append(question)
        return self._answer


@pytest.fixture
async def client_with_data():
    repo = InMemoryProductRepository()
    search = InMemorySearch()
    p = _product()
    await repo.save(p)
    await search.index(p)
    app = create_app(repo=repo, search=search)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, repo


@pytest.fixture
async def client_with_fetcher_and_agent():
    repo = InMemoryProductRepository()
    search = InMemorySearch()
    fetcher = FakeFetcher((FIXTURES / "sample_product.html").read_text())
    agent = FakeAgent(answer="The Echo Dot costs $49.99.")
    app = create_app(repo=repo, search=search, fetcher=fetcher, agent=agent)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, repo, agent


@pytest.fixture
async def client_minimal():
    """No fetcher, no agent — exercises 503 paths."""
    app = create_app(repo=InMemoryProductRepository(), search=InMemorySearch())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealth:
    async def test_health_ok(self, client_minimal):
        resp = await client_minimal.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestScrape:
    async def test_scrape_schedules_and_persists(self, client_with_fetcher_and_agent):
        client, repo, _ = client_with_fetcher_and_agent
        resp = await client.post(
            "/scrape", json={"asin": "B08N5WRWNW", "country_code": "US"}
        )
        assert resp.status_code == 202
        assert resp.json() == {
            "asin": "B08N5WRWNW",
            "country_code": "US",
            "status": "queued",
        }
        # Background task ran before the request lifecycle closed.
        stored = await repo.get("B08N5WRWNW", "US")
        assert stored is not None
        assert stored.title

    async def test_scrape_lower_case_country_normalised(self, client_with_fetcher_and_agent):
        client, _, _ = client_with_fetcher_and_agent
        resp = await client.post(
            "/scrape", json={"asin": "B08N5WRWNW", "country_code": "us"}
        )
        assert resp.status_code == 202
        assert resp.json()["country_code"] == "US"

    async def test_scrape_invalid_asin_rejected(self, client_with_fetcher_and_agent):
        client, _, _ = client_with_fetcher_and_agent
        resp = await client.post(
            "/scrape", json={"asin": "too-short", "country_code": "US"}
        )
        assert resp.status_code == 422

    async def test_scrape_503_without_fetcher(self, client_minimal):
        resp = await client_minimal.post(
            "/scrape", json={"asin": "B08N5WRWNW", "country_code": "US"}
        )
        assert resp.status_code == 503


class TestProducts:
    async def test_get_product_hit(self, client_with_data):
        client, _ = client_with_data
        resp = await client.get("/products/B08N5WRWNW/US")
        assert resp.status_code == 200
        body = resp.json()
        assert body["asin"] == "B08N5WRWNW"
        assert body["title"] == "Echo Dot smart speaker"

    async def test_get_product_miss_returns_404(self, client_with_data):
        client, _ = client_with_data
        resp = await client.get("/products/B00NOTFOUND/US")
        # Pydantic length constraint on path? No — path params bypass it. Good.
        assert resp.status_code == 404

    async def test_list_products(self, client_with_data):
        client, _ = client_with_data
        resp = await client.get("/products")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    async def test_price_history(self, client_with_data):
        client, _ = client_with_data
        resp = await client.get("/products/B08N5WRWNW/US/history")
        assert resp.status_code == 200
        history = resp.json()
        assert len(history) == 1
        assert history[0]["price"] == "49.99"


class TestSearch:
    async def test_search_returns_hits(self, client_with_data):
        client, _ = client_with_data
        resp = await client.get("/search", params={"q": "speaker"})
        assert resp.status_code == 200
        hits = resp.json()
        assert len(hits) == 1
        assert hits[0]["asin"] == "B08N5WRWNW"
        assert hits[0]["score"] > 0

    async def test_search_empty_when_no_match(self, client_with_data):
        client, _ = client_with_data
        resp = await client.get("/search", params={"q": "submarine"})
        assert resp.status_code == 200
        assert resp.json() == []


class TestAsk:
    async def test_ask_calls_agent(self, client_with_fetcher_and_agent):
        client, _, agent = client_with_fetcher_and_agent
        resp = await client.post("/ask", json={"question": "What's the price?"})
        assert resp.status_code == 200
        assert resp.json() == {"answer": "The Echo Dot costs $49.99."}
        assert agent.questions == ["What's the price?"]

    async def test_ask_503_without_agent(self, client_minimal):
        resp = await client_minimal.post("/ask", json={"question": "anything"})
        assert resp.status_code == 503

    async def test_ask_rejects_empty(self, client_with_fetcher_and_agent):
        client, _, _ = client_with_fetcher_and_agent
        resp = await client.post("/ask", json={"question": ""})
        assert resp.status_code == 422
