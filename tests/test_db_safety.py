"""The guard that stands between the test fixtures and ``DROP TABLE``.

These tests need no database: the guard's decision is pure, and the reset is
exercised against a recording stand-in to prove nothing destructive runs when
the guard refuses.
"""

from __future__ import annotations

from typing import cast

import pytest

from app.db import Connection
from tests.db_safety import (
    DatabaseTarget,
    UnsafeTestDatabaseError,
    check_disposable,
    reset_test_schema,
    target_from_url,
)

APP_URL = "postgresql://appuser:hunter2@localhost:5433/fintech"


def test_a_test_database_distinct_from_the_application_is_accepted() -> None:
    check_disposable(DatabaseTarget("local", "5433", "fintech_test"), APP_URL)
    check_disposable(DatabaseTarget("local", "5433", "fintech_test"), None)


def test_a_database_without_the_test_suffix_is_rejected() -> None:
    with pytest.raises(UnsafeTestDatabaseError) as raised:
        check_disposable(DatabaseTarget("local", "5433", "fintech"), None)

    assert "'fintech'" in str(raised.value)


@pytest.mark.parametrize(
    "app_url",
    [
        "postgresql://localhost:5433/fintech_test",
        "postgresql://appuser:hunter2@127.0.0.1:5433/fintech_test",
        "host=/tmp port=5433 dbname=fintech_test",
    ],
)
def test_the_application_database_is_rejected_even_with_a_test_name(
    app_url: str,
) -> None:
    """Loopback names and the unix socket all reach the same local server."""
    with pytest.raises(UnsafeTestDatabaseError) as raised:
        check_disposable(DatabaseTarget("local", "5433", "fintech_test"), app_url)

    assert "hunter2" not in str(raised.value)
    assert "postgresql://" not in str(raised.value)


def test_the_same_name_on_another_server_or_port_is_not_the_same_target() -> None:
    test = DatabaseTarget("local", "5433", "fintech_test")

    check_disposable(test, "postgresql://localhost:5432/fintech_test")
    check_disposable(test, "postgresql://db.internal:5433/fintech_test")


def test_url_defaults_follow_libpq(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PGHOST", "PGPORT", "PGUSER", "PGDATABASE"):
        monkeypatch.delenv(name, raising=False)

    assert target_from_url("postgresql://127.0.0.1/fintech") == DatabaseTarget(
        "local", "5432", "fintech"
    )
    assert target_from_url("postgresql://someone@db.example.com") == DatabaseTarget(
        "db.example.com", "5432", "someone"
    )


def test_an_unparseable_application_url_is_refused_without_echoing_it() -> None:
    with pytest.raises(UnsafeTestDatabaseError) as raised:
        check_disposable(
            DatabaseTarget("local", "5433", "fintech_test"),
            "postgresql://u:hunter2@host:notaport:/x?bad==",
        )

    assert "hunter2" not in str(raised.value)


class _Cursor:
    def __init__(self, row: tuple[str]) -> None:
        self.row = row

    async def fetchone(self) -> tuple[str]:
        return self.row


class _Info:
    host = "127.0.0.1"
    port = 5433


class _RecordingConnection:
    """Answers ``current_database()`` and records every statement it is sent."""

    info = _Info()

    def __init__(self, dbname: str) -> None:
        self.dbname = dbname
        self.statements: list[str] = []

    async def execute(self, query: object, *args: object) -> _Cursor:
        self.statements.append(str(query))
        return _Cursor((self.dbname,))

    async def commit(self) -> None:
        self.statements.append("COMMIT")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("dbname", "app_url"),
    [
        pytest.param("fintech", None, id="no-test-suffix"),
        pytest.param(
            "fintech_test", "postgresql://localhost:5433/fintech_test", id="app-db"
        ),
    ],
)
async def test_a_refused_database_is_never_reset(
    monkeypatch: pytest.MonkeyPatch, dbname: str, app_url: str | None
) -> None:
    if app_url is None:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("DATABASE_URL", app_url)
    conn = _RecordingConnection(dbname)

    with pytest.raises(UnsafeTestDatabaseError):
        await reset_test_schema(cast(Connection, conn))

    assert conn.statements == ["SELECT current_database()"]
