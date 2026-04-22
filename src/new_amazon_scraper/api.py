"""HTTP API.

`create_app(repo, search, fetcher, agent)` is a pure factory — no I/O at
construction time. Tests pass in-memory adapters; production wires real ones
through `create_production_app()`'s lifespan.

Why a factory + lifespan split?
  - The factory composes dependencies. Trivial to test.
  - The lifespan owns resource creation/teardown (engines, HTTP clients).
    Production code goes through it; tests skip it.

Why FastAPI BackgroundTasks instead of Inngest/Celery?
  - For a single-process API answering one user, a background task is enough.
  - Trade-off: tasks die with the process — fine for a personal tool, not for
    durable work queues. If you need retries/scheduling, swap in Arq or RQ.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .agent import Agent, AgentTools
from .config import Settings
from .db import create_engine, create_session_factory
from .fetcher import AmazonFetcher
from .product import PricePoint, Product
from .repo import PostgresProductRepository, ProductRepository
from .scraper import HtmlFetcher, scrape_product
from .search import OpenAIPgVectorSearch, Search

log = logging.getLogger(__name__)


# --- Request / response schemas ---------------------------------------------

class ScrapeRequest(BaseModel):
    asin: str = Field(min_length=10, max_length=10, pattern=r"^[A-Z0-9]{10}$")
    country_code: str = Field(min_length=2, max_length=2)


class ScrapeAccepted(BaseModel):
    asin: str
    country_code: str
    status: str = "queued"


class SearchHitResponse(BaseModel):
    asin: str
    country_code: str
    title: str | None
    score: float


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str


# --- Background work --------------------------------------------------------

async def _scrape_and_store(
    fetcher: HtmlFetcher,
    repo: ProductRepository,
    search: Search,
    asin: str,
    country_code: str,
) -> None:
    """The job a `/scrape` request schedules. Logs and swallows failures —
    background tasks have no caller to raise to."""
    try:
        product = await scrape_product(fetcher, asin, country_code)
        if not product.is_valid():
            log.warning("scrape produced unparseable product asin=%s cc=%s", asin, country_code)
            return
        await repo.save(product)
        await search.index(product)
    except Exception:
        log.exception("background scrape failed asin=%s cc=%s", asin, country_code)


# --- Dependency providers ---------------------------------------------------
# These read from app.state so the same endpoints work for any wiring (test or prod).

def _repo(request: Request) -> ProductRepository:
    return request.app.state.repo


def _search(request: Request) -> Search:
    return request.app.state.search


def _fetcher_or_503(request: Request) -> HtmlFetcher:
    fetcher = getattr(request.app.state, "fetcher", None)
    if fetcher is None:
        raise HTTPException(status_code=503, detail="scrape disabled: no fetcher configured")
    return fetcher


def _agent_or_503(request: Request) -> Agent:
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(status_code=503, detail="agent disabled: no LLM configured")
    return agent


RepoDep = Annotated[ProductRepository, Depends(_repo)]
SearchDep = Annotated[Search, Depends(_search)]
FetcherDep = Annotated[HtmlFetcher, Depends(_fetcher_or_503)]
AgentDep = Annotated[Agent, Depends(_agent_or_503)]


# --- App factory ------------------------------------------------------------

def create_app(
    *,
    repo: ProductRepository,
    search: Search,
    fetcher: HtmlFetcher | None = None,
    agent: Agent | None = None,
) -> FastAPI:
    """Compose an app from already-built dependencies."""
    app = FastAPI(title="Amazon Scraper API")
    app.state.repo = repo
    app.state.search = search
    app.state.fetcher = fetcher
    app.state.agent = agent
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/scrape", status_code=202, response_model=ScrapeAccepted)
    async def scrape(
        body: ScrapeRequest,
        background: BackgroundTasks,
        repo: RepoDep,
        search: SearchDep,
        fetcher: FetcherDep,
    ) -> ScrapeAccepted:
        # Pydantic already canonicalised asin; canonicalise country_code here.
        cc = body.country_code.upper()
        background.add_task(_scrape_and_store, fetcher, repo, search, body.asin, cc)
        return ScrapeAccepted(asin=body.asin, country_code=cc)

    @app.get("/products", response_model=list[Product])
    async def list_products(repo: RepoDep, limit: int = 100) -> list[Product]:
        return await repo.list_all(limit=limit)

    @app.get("/products/{asin}/{country_code}", response_model=Product)
    async def get_product(asin: str, country_code: str, repo: RepoDep) -> Product:
        product = await repo.get(asin, country_code)
        if product is None:
            raise HTTPException(status_code=404, detail="product not found")
        return product

    @app.get(
        "/products/{asin}/{country_code}/history",
        response_model=list[PricePoint],
    )
    async def price_history(
        asin: str,
        country_code: str,
        repo: RepoDep,
        limit: int = 50,
    ) -> list[PricePoint]:
        return await repo.get_price_history(asin, country_code, limit=limit)

    @app.get("/search", response_model=list[SearchHitResponse])
    async def search_endpoint(
        q: str,
        search: SearchDep,
        limit: int = 10,
    ) -> list[SearchHitResponse]:
        hits = await search.query(q, limit=limit)
        return [
            SearchHitResponse(
                asin=h.product.asin,
                country_code=h.product.country_code,
                title=h.product.title,
                score=h.score,
            )
            for h in hits
        ]

    @app.post("/ask", response_model=AskResponse)
    async def ask(body: AskRequest, agent: AgentDep) -> AskResponse:
        answer = await agent.ask(body.question)
        return AskResponse(answer=answer)


# --- Production wiring ------------------------------------------------------

def create_production_app(settings: Settings | None = None) -> FastAPI:
    """The app uvicorn serves. Builds real adapters in a lifespan."""
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url)
        session_factory = create_session_factory(engine)
        openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
        fetcher = AmazonFetcher(proxy_url=settings.thordata_proxy_url)

        repo = PostgresProductRepository(session_factory)
        search = OpenAIPgVectorSearch(
            session_factory=session_factory,
            openai_client=openai_client,
            embedding_model=settings.openai_embedding_model,
        )
        agent = Agent(
            tools=AgentTools(repo=repo, search=search, fetcher=fetcher),
            openai_client=openai_client,
            model=settings.openai_chat_model,
        )

        app.state.repo = repo
        app.state.search = search
        app.state.fetcher = fetcher
        app.state.agent = agent
        try:
            yield
        finally:
            await fetcher.aclose()
            await openai_client.close()
            await engine.dispose()

    app = FastAPI(title="Amazon Scraper API", lifespan=lifespan)
    _register_routes(app)
    return app
