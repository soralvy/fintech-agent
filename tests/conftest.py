"""Shared test fixtures.

Database tests run against a real PostgreSQL with pgvector; the vector SQL is
never mocked (docs/DECISIONS.md section 20.2). They are skipped unless
``TEST_DATABASE_URL`` names a database that is safe to wipe -- a separate
variable from ``DATABASE_URL`` on purpose, so pointing the application at a
database never puts that database's contents at risk.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest

from app.config import DatabaseConfig
from app.db import Connection, Pool, apply_migration, create_pool

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"

EMBEDDING_DIMENSIONS = 1536


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Run async tests on asyncio via the anyio plugin, which ships with anyio."""
    return "asyncio"


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set")
    yield url


@pytest.fixture
async def pool(database_url: str) -> AsyncIterator[Pool]:
    pool = create_pool(DatabaseConfig(url=database_url))
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def db(pool: Pool) -> AsyncIterator[Connection]:
    """A connection against a freshly migrated, empty schema.

    Dropping lives here rather than in application code: destroying tables is a
    test concern, and the migration itself only ever creates.
    """
    async with pool.connection() as conn:
        await conn.execute("DROP TABLE IF EXISTS document_chunks, documents CASCADE")
        await apply_migration(conn)
        await conn.commit()
        yield conn


def embedding(*, hot_index: int, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A deterministic unit vector with a single non-zero dimension.

    Two such vectors are identical when the indexes match and orthogonal
    otherwise, which makes cosine distance exactly 0.0 or 1.0 -- no fixture
    corpus and no embedding provider needed.
    """
    values = [0.0] * dimensions
    values[hot_index] = 1.0
    return values
