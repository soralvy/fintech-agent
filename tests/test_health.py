"""HTTP boundary tests for the health endpoint.

Most use a fake pool rather than a database: the point is the HTTP contract,
including the failure branch, which a real database will not produce on demand.
The outage-timing regression test instead runs the real lifespan and pool
against a port with nothing listening, so it needs no database either.
Real vector SQL is exercised in ``test_retrieval_db.py``.
"""

from __future__ import annotations

import socket
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.config import DEFAULT_POOL_TIMEOUT_SECONDS, DatabaseConfig
from app.db import create_pool
from app.main import app, get_pool


class FakeConnection:
    """Records the statement it was asked to run, or raises instead."""

    def __init__(self, error: psycopg.Error | None) -> None:
        self.error = error
        self.executed: list[str] = []

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.executed.append(query)


class FakePool:
    """Stands in for the psycopg pool in ``get_pool``."""

    def __init__(self, error: psycopg.Error | None = None) -> None:
        self.connection_obj = FakeConnection(error)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[FakeConnection]:
        yield self.connection_obj


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose pool is a fake, so lifespan never opens a real one."""
    pool = FakePool()
    app.dependency_overrides[get_pool] = lambda: pool
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_queries_the_database() -> None:
    """The endpoint must actually probe the database, not just claim it did."""
    pool = FakePool()
    app.dependency_overrides[get_pool] = lambda: pool
    try:
        TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert pool.connection_obj.executed == ["SELECT 1"]


def test_health_reports_503_when_the_database_is_unreachable() -> None:
    pool = FakePool(error=psycopg.OperationalError("connection refused"))
    app.dependency_overrides[get_pool] = lambda: pool
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503


def test_health_failure_does_not_leak_connection_details() -> None:
    """A driver error can carry the DSN; it must not reach the client."""
    secret = "postgresql://user:hunter2@db.internal:5432/fintech"
    pool = FakePool(error=psycopg.OperationalError(f"could not connect to {secret}"))
    app.dependency_overrides[get_pool] = lambda: pool
    try:
        response = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()

    assert "hunter2" not in response.text
    assert "db.internal" not in response.text
    assert response.json() == {"detail": "database unavailable"}


def _unused_local_port() -> int:
    """A localhost port with nothing listening, so connections are refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def test_health_returns_503_within_the_pool_timeout_when_database_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an outage used to take psycopg_pool's 30-second default.

    Runs the real lifespan and a real pool -- no fakes -- against a port with
    nothing listening. The timeout is shrunk so the test is fast; the bound
    being honoured is what matters, not its production value.
    """
    timeout = 0.5
    unreachable = DatabaseConfig(
        url=f"postgresql://appuser:s3cretpw@127.0.0.1:{_unused_local_port()}/fintech",
        pool_timeout_seconds=timeout,
    )
    monkeypatch.setattr(
        DatabaseConfig, "from_env", classmethod(lambda cls: unreachable)
    )

    with TestClient(app) as client:
        started = time.monotonic()
        response = client.get("/health")
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}
    assert "s3cretpw" not in response.text
    assert elapsed < timeout + 2.0, (
        f"took {elapsed:.1f}s; the pool timeout is not applied"
    )


def test_pool_uses_the_configured_timeout() -> None:
    config = DatabaseConfig(url="postgresql://localhost/unused")

    pool = create_pool(config)

    assert pool.timeout == DEFAULT_POOL_TIMEOUT_SECONDS
    assert DEFAULT_POOL_TIMEOUT_SECONDS < 30.0, "must stay below psycopg_pool's default"
