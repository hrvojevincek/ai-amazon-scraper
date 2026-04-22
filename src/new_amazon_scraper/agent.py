"""Q&A agent over products.

`Agent.ask(question)` runs an LLM-driven tool-calling loop over the
repo/search/scraper services. The LLM decides which tools to call; we
dispatch them, feed results back, and repeat until the LLM stops asking.

Two classes:
  AgentTools  — pure dispatch: tool name + args → JSON result string. Testable.
  Agent       — the LLM loop. Thin. Not unit-tested; covered by integration.
"""

import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from .product import Product
from .repo import ProductRepository
from .scraper import HtmlFetcher, scrape_product
from .search import Search

SYSTEM_PROMPT = """You answer questions about Amazon products stored in a database.

You have tools to:
- search_products: semantic search over indexed products
- get_product: fetch one product by ASIN and country code
- get_price_history: show historical prices for a product
- scrape_product (if available): scrape a live Amazon page and save it

Rules:
- Always cite ASIN and country code when referring to a product.
- If a tool returns no results, say so — don't invent products.
- Prefer get_product over scrape_product if the product is already stored.
- Be concise. Prices in the stored currency; don't convert.
"""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


# --- Tool dispatch (testable) ------------------------------------------------

class AgentTools:
    """Turns `(tool_name, args)` into a JSON string the LLM can read back."""

    def __init__(
        self,
        *,
        repo: ProductRepository,
        search: Search,
        fetcher: HtmlFetcher | None = None,
    ) -> None:
        self._repo = repo
        self._search = search
        self._fetcher = fetcher

    def specs(self) -> list[ToolSpec]:
        specs = [
            ToolSpec(
                name="search_products",
                description="Semantic search over indexed products. Returns ranked matches.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "natural-language query"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            ToolSpec(
                name="get_product",
                description="Fetch a specific product by ASIN and country code.",
                parameters={
                    "type": "object",
                    "properties": {
                        "asin": {"type": "string"},
                        "country_code": {
                            "type": "string",
                            "description": "ISO 3166-1 alpha-2, e.g. US, DE, GB",
                        },
                    },
                    "required": ["asin", "country_code"],
                },
            ),
            ToolSpec(
                name="get_price_history",
                description="Historical price observations for a product, newest first.",
                parameters={
                    "type": "object",
                    "properties": {
                        "asin": {"type": "string"},
                        "country_code": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["asin", "country_code"],
                },
            ),
        ]
        if self._fetcher is not None:
            specs.append(
                ToolSpec(
                    name="scrape_product",
                    description=(
                        "Scrape a product live from Amazon and save it. "
                        "Use only when the product isn't already in the database."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "asin": {"type": "string"},
                            "country_code": {"type": "string"},
                        },
                        "required": ["asin", "country_code"],
                    },
                )
            )
        return specs

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "search_products":
                return await self._search_products(args)
            if name == "get_product":
                return await self._get_product(args)
            if name == "get_price_history":
                return await self._get_price_history(args)
            if name == "scrape_product" and self._fetcher is not None:
                return await self._scrape_product(args)
            return json.dumps({"error": f"unknown tool: {name}"})
        except Exception as exc:  # noqa: BLE001
            # Return errors to the LLM as content so it can recover rather than
            # crash the whole conversation.
            return json.dumps({"error": str(exc), "tool": name})

    async def _search_products(self, args: dict[str, Any]) -> str:
        hits = await self._search.query(args["query"], limit=args.get("limit", 5))
        return json.dumps([
            {
                "asin": h.product.asin,
                "country_code": h.product.country_code,
                "title": h.product.title,
                "price": str(h.product.price) if h.product.price else None,
                "currency": h.product.currency,
                "score": round(h.score, 3),
            }
            for h in hits
        ])

    async def _get_product(self, args: dict[str, Any]) -> str:
        product = await self._repo.get(args["asin"], args["country_code"])
        if product is None:
            return "null"
        return json.dumps(_summary(product))

    async def _get_price_history(self, args: dict[str, Any]) -> str:
        history = await self._repo.get_price_history(
            args["asin"],
            args["country_code"],
            limit=args.get("limit", 20),
        )
        return json.dumps([
            {
                "scraped_at": p.scraped_at.isoformat(),
                "price": str(p.price),
                "currency": p.currency,
            }
            for p in history
        ])

    async def _scrape_product(self, args: dict[str, Any]) -> str:
        assert self._fetcher is not None
        product = await scrape_product(
            self._fetcher, args["asin"], args["country_code"]
        )
        if not product.is_valid():
            return json.dumps({"error": "product could not be parsed", "asin": args["asin"]})
        await self._repo.save(product)
        await self._search.index(product)
        return json.dumps(_summary(product))


def _summary(p: Product) -> dict[str, Any]:
    return {
        "asin": p.asin,
        "country_code": p.country_code,
        "title": p.title,
        "brand": p.brand,
        "price": str(p.price) if p.price else None,
        "currency": p.currency,
        "rating": p.rating,
        "review_count": p.review_count,
    }


# --- LLM loop (thin) ---------------------------------------------------------

class Agent:
    """Runs an OpenAI tool-calling conversation to answer a user question."""

    def __init__(
        self,
        *,
        tools: AgentTools,
        openai_client: AsyncOpenAI,
        model: str = "gpt-4o-mini",
        max_iterations: int = 5,
    ) -> None:
        self._tools = tools
        self._client = openai_client
        self._model = model
        self._max_iterations = max_iterations

    async def ask(self, question: str) -> str:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        tool_defs = [_to_openai_tool(s) for s in self._tools.specs()]

        for _ in range(self._max_iterations):
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tool_defs,
            )
            msg = resp.choices[0].message

            if not msg.tool_calls:
                return msg.content or ""

            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                result = await self._tools.execute(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return "I ran out of reasoning steps before reaching an answer."


def _to_openai_tool(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }
