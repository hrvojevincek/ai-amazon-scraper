"""Integration-test fixtures.

`pg_container` is session-scoped: spawning Postgres + running migrations is
expensive (~10s), so we do it once. `session_factory` is per-test and
truncates `products` (cascades to `price_history`) before yielding, so each
test gets a clean slate without rebooting the container.

We use the `pgvector/pgvector:pg16` image because the standard `postgres`
image doesn't ship the `vector` extension our 0002 migration needs.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"
ALEMBIC_DIR = PROJECT_ROOT / "alembic"


def _ensure_docker_host() -> None:
    """Pick a working DOCKER_HOST when none is set.

    On macOS the default `/var/run/docker.sock` may symlink to a daemon
    that isn't running (OrbStack vs Docker Desktop), which surfaces as a
    cryptic FileNotFoundError from the Python docker client. Try the
    standard Docker Desktop path before giving up.
    """
    if os.environ.get("DOCKER_HOST"):
        return
    candidates = [
        Path.home() / ".docker" / "run" / "docker.sock",
        Path.home() / ".orbstack" / "run" / "docker.sock",
        Path("/var/run/docker.sock"),
    ]
    for sock in candidates:
        if sock.exists():
            os.environ["DOCKER_HOST"] = f"unix://{sock}"
            return


@pytest.fixture(scope="session")
def pg_container() -> Iterator[str]:
    """Start a pgvector container, run migrations, return its async URL."""
    _ensure_docker_host()
    container = PostgresContainer(
        "pgvector/pgvector:pg16",
        username="postgres",
        password="postgres",
        dbname="amazon_scraper_test",
        driver="asyncpg",
    )
    container.start()
    try:
        url = container.get_connection_url()
        # env.py reads Settings(), which reads DATABASE_URL with higher
        # priority than the .env file. So setting it here points alembic
        # at the container.
        os.environ["DATABASE_URL"] = url

        cfg = Config(str(ALEMBIC_INI))
        # Override script_location so alembic finds env.py + versions/
        # regardless of pytest's cwd.
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")

        yield url
    finally:
        container.stop()


@pytest.fixture
async def session_factory(
    pg_container: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Fresh engine per test + truncated tables. Cheap once the container is up."""
    engine = create_async_engine(pg_container, pool_pre_ping=True)
    async with engine.begin() as conn:
        # CASCADE wipes price_history via the FK.
        await conn.execute(text("TRUNCATE TABLE products CASCADE"))
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield sf
    finally:
        await engine.dispose()
