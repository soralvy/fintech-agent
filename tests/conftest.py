"""Shared test fixtures.

Database tests run against a real PostgreSQL with pgvector; the vector SQL is
never mocked (docs/DECISIONS.md section 20.2). They are skipped unless
``TEST_DATABASE_URL`` is set -- a separate variable from ``DATABASE_URL`` on
purpose. Every reset of that database goes through
``tests.db_safety.reset_test_schema``, which refuses to drop anything unless
the live connection is a ``*_test`` database distinct from the application's.

LangSmith tracing variables are removed from the environment at import, before
any test module is collected or any graph is built: LangSmith caches its first
read of them for the whole process (docs/TECH_BASELINE.md section 7).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import NoReturn

import pytest
import tiktoken
import tiktoken.load

from app.config import TRACING_ENV_VARS, DatabaseConfig
from app.db import Connection, Pool, create_pool
from tests.db_safety import reset_test_schema

TEST_DATABASE_URL_ENV = "TEST_DATABASE_URL"

EMBEDDING_DIMENSIONS = 1536

for _name in TRACING_ENV_VARS:
    os.environ.pop(_name, None)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    """Run async tests on asyncio via the anyio plugin, which ships with anyio."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_tracing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset every tracing variable per test, so none leaks into the next."""
    for name in TRACING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_tiktoken_encoding_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test reaches tiktoken's encoding loader.

    Loading ``cl100k_base`` can download it, and automated tests must make no
    network call. Tests use ``FakeTokenizer``, or inject a loader into the
    tiktoken adapter; reaching the real loader is a test bug.
    """

    def refuse(*args: object, **kwargs: object) -> NoReturn:
        raise OSError("tiktoken encoding data is not available to tests")

    monkeypatch.setattr(tiktoken, "get_encoding", refuse)
    monkeypatch.setattr(tiktoken.load, "read_file", refuse)


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
    """A connection against a freshly migrated, empty schema."""
    async with pool.connection() as conn:
        await reset_test_schema(conn)
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
