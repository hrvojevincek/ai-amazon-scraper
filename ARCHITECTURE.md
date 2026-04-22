# Amazon Scraper — Architecture

## System Architecture

```mermaid
graph TB
    subgraph "External Services"
        AMAZON[Amazon Marketplaces<br/>18 country domains]
        OPENAI[OpenAI API<br/>Embeddings + Chat]
        PROXY[Thordata Proxy<br/>optional]
    end

    subgraph "Application Layer"
        STREAMLIT[Streamlit UI<br/>ui.py]
        API[FastAPI<br/>api.py]
        BGTASK[FastAPI<br/>BackgroundTasks]
    end

    subgraph "Core Modules"
        SCRAPER[scraper.py<br/>fetcher + parser]
        AGENT[agent.py<br/>OpenAI tool-calling loop]
        SEARCH[search.py<br/>semantic search]
        REPO[repo.py<br/>Protocol + Postgres impl]
    end

    subgraph "Storage"
        PG[(Postgres + pgvector<br/>products, price_history)]
    end

    %% UI -> API
    STREAMLIT -->|HTTP via ui_client.py| API

    %% API endpoints
    API -->|POST /scrape| BGTASK
    API -->|GET /products| REPO
    API -->|GET /search| SEARCH
    API -->|POST /ask| AGENT

    %% Scrape pipeline
    BGTASK -->|1. fetch HTML| SCRAPER
    SCRAPER -->|via httpx| PROXY
    PROXY -->|or direct| AMAZON
    BGTASK -->|2. upsert| REPO
    BGTASK -->|3. index embedding| SEARCH

    %% Search pipeline
    SEARCH -->|embed query| OPENAI
    SEARCH -->|vector similarity| PG

    %% Agent pipeline
    AGENT -->|chat + tools| OPENAI
    AGENT -->|tool: search_products| SEARCH
    AGENT -->|tool: get_product / history| REPO
    AGENT -->|tool: scrape_product| SCRAPER

    %% Persistence
    REPO --> PG
    SEARCH -->|write embeddings| PG

    style AMAZON fill:#ff9900
    style OPENAI fill:#10a37f
    style PROXY fill:#4a90e2
    style API fill:#009688
    style STREAMLIT fill:#ff4b4b
    style BGTASK fill:#6366f1
    style PG fill:#336791
    style AGENT fill:#ff6b6b
    style SCRAPER fill:#ffa726
    style SEARCH fill:#00a8ff
    style REPO fill:#8e44ad
```

## Data Flow Diagrams

### Scrape Flow (`POST /scrape`)

```mermaid
sequenceDiagram
    participant User
    participant UI as Streamlit UI
    participant API as FastAPI
    participant BG as BackgroundTask
    participant Fetcher as AmazonFetcher
    participant Amazon
    participant Parser
    participant Repo as PostgresRepository
    participant Search as OpenAIPgVectorSearch
    participant OpenAI
    participant PG as Postgres+pgvector

    User->>UI: Enter ASIN + country
    UI->>API: POST /scrape {asin, country_code}
    API-->>UI: 202 Accepted (queued)
    API->>BG: schedule _scrape_and_store

    BG->>Fetcher: fetch_html(asin, cc)
    Fetcher->>Amazon: GET /dp/{asin} (via proxy, retry 2x)
    Amazon-->>Fetcher: HTML
    Fetcher-->>BG: raw HTML
    BG->>Parser: parse_product(html, asin, cc)
    Parser-->>BG: Product

    BG->>Repo: save(product)
    Repo->>PG: UPSERT products + INSERT price_history

    BG->>Search: index(product)
    Search->>OpenAI: embeddings.create(title + brand + ...)
    OpenAI-->>Search: vector[1536]
    Search->>PG: UPDATE products SET embedding = ...
```

### Semantic Search Flow (`GET /search`)

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI
    participant Search as OpenAIPgVectorSearch
    participant OpenAI
    participant PG as Postgres+pgvector

    User->>API: GET /search?q=...&limit=10
    API->>Search: query(q, limit)
    Search->>OpenAI: embeddings.create(q)
    OpenAI-->>Search: query vector
    Search->>PG: SELECT ... ORDER BY embedding <=> $1 LIMIT N
    PG-->>Search: ranked rows
    Search-->>API: list[SearchHit]
    API-->>User: JSON hits
```

### Agent Q&A Flow (`POST /ask`)

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI
    participant Agent
    participant OpenAI
    participant Tools as AgentTools
    participant Search
    participant Repo
    participant Fetcher

    User->>API: POST /ask {question}
    API->>Agent: ask(question)

    loop up to max_iterations (5)
        Agent->>OpenAI: chat.completions.create(messages, tools)
        OpenAI-->>Agent: message + tool_calls?

        alt no tool calls
            Agent-->>API: final answer
        else tool calls present
            par per tool call
                Agent->>Tools: execute(name, args)
                alt search_products
                    Tools->>Search: query()
                else get_product / get_price_history
                    Tools->>Repo: get() / get_price_history()
                else scrape_product
                    Tools->>Fetcher: scrape_product()
                end
                Tools-->>Agent: JSON result
            end
        end
    end

    API-->>User: {answer}
```

## Key Components

| Component      | Technology              | Purpose                                                           |
|----------------|-------------------------|-------------------------------------------------------------------|
| **UI**         | Streamlit               | Browser frontend (`ui.py` + `ui_client.py` HTTP client)          |
| **API**        | FastAPI + uvicorn       | REST endpoints, factory + lifespan wiring                         |
| **Background** | FastAPI BackgroundTasks | In-process async jobs for `/scrape` (no external queue)           |
| **Fetcher**    | httpx (async)           | Amazon HTML retrieval, retries, proxy support, 18 marketplaces   |
| **Parser**     | BeautifulSoup4 + lxml   | HTML → `Product` domain object; currency/price normalization      |
| **Repo**       | SQLAlchemy async        | `ProductRepository` Protocol; `InMemory` + `Postgres` adapters    |
| **Database**   | Postgres 16 + pgvector  | Products, price history, 1536-dim embeddings in one store         |
| **Search**     | OpenAI + pgvector       | `text-embedding-3-small` + cosine similarity via `<=>` operator   |
| **Agent**      | OpenAI tool-calling     | `gpt-4o-mini` loop; dispatches to repo/search/scraper tools       |
| **Migrations** | Alembic                 | `0001_initial`, `0002_add_embedding`                              |
| **Config**     | Pydantic Settings       | `.env`-driven; DB URL, OpenAI key, proxy URL                      |

## Technology Stack

- **Backend**: Python 3.13, FastAPI, uvicorn, SQLAlchemy (async), asyncpg
- **Database**: Postgres 16 + pgvector extension
- **AI/ML**: OpenAI SDK (`AsyncOpenAI`) — `text-embedding-3-small` (1536d), `gpt-4o-mini`
- **Scraping**: httpx (async, retries, proxy), BeautifulSoup4, lxml
- **UI**: Streamlit
- **Migrations**: Alembic
- **Validation**: Pydantic (Decimal prices, ISO country codes, ASIN regex)
- **Testing**: pytest, pytest-asyncio, testcontainers (pgvector integration tests)
- **Infra**: Docker Compose (Postgres + pgvector)

## Architectural Choices

- **No external queue** (Inngest/Celery/RQ). `BackgroundTasks` is enough for a single-process personal tool. Trade-off: tasks die with the process — swap in Arq/RQ if durability matters. See [api.py](src/new_amazon_scraper/api.py) header.
- **One datastore, not two.** pgvector stores embeddings alongside product rows — no MongoDB + Qdrant split. Fewer moving parts, transactional consistency for free.
- **No LangChain.** Direct OpenAI tool-calling loop in [agent.py](src/new_amazon_scraper/agent.py). Thin, testable, no framework churn.
- **Protocol-based adapters.** `ProductRepository`, `HtmlFetcher`, `Search` are `typing.Protocol`s. In-memory doubles make unit tests trivial; production wiring lives only in `create_production_app()`.
- **Factory + lifespan split.** `create_app()` composes dependencies for tests; `create_production_app()` owns resource lifecycle (engine, HTTP client, OpenAI client).
