"""The one guarded reset for the disposable integration-test database.

Integration tests drop and recreate both application tables. Every reset goes
through ``reset_test_schema``, which proves the connection is a disposable
test database before any destructive statement runs:

- ``SELECT current_database()`` must name a database ending in ``_test``;
- when ``DATABASE_URL`` is set, the connection must not reach the same server
  and database as the application.

Errors name the database at most, never a connection string or password.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.db import Connection, apply_migration

APP_DATABASE_URL_ENV = "DATABASE_URL"
TEST_DATABASE_SUFFIX = "_test"

# libpq's defaults when a connection string leaves host or port out.
_DEFAULT_PORT = "5432"
# Spellings that all reach the local server: loopback names, and unix-socket
# directories (an empty host, or an absolute path).
_LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})


class UnsafeTestDatabaseError(RuntimeError):
    """The test connection is not provably a disposable test database."""


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    """Where a connection lands: server host, port, and database name."""

    host: str
    port: str
    dbname: str


def _normalize_host(host: str) -> str:
    return "local" if host in _LOCAL_HOSTS or host.startswith("/") else host.lower()


def target_from_url(url: str) -> DatabaseTarget:
    """Resolve a connection string the way libpq fills in its defaults.

    Raises:
        UnsafeTestDatabaseError: the string cannot be parsed, so the targets
            cannot be compared. The message never includes the string.
    """
    try:
        params = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        raise UnsafeTestDatabaseError(
            f"{APP_DATABASE_URL_ENV} cannot be parsed, so the test database "
            "cannot be proven distinct from it"
        ) from None
    host = str(params.get("host") or os.environ.get("PGHOST", ""))
    port = str(params.get("port") or os.environ.get("PGPORT", "") or _DEFAULT_PORT)
    user = str(params.get("user") or os.environ.get("PGUSER", "") or getpass.getuser())
    dbname = str(params.get("dbname") or os.environ.get("PGDATABASE", "") or user)
    return DatabaseTarget(host=_normalize_host(host), port=port, dbname=dbname)


def check_disposable(test: DatabaseTarget, app_database_url: str | None) -> None:
    """Refuse a test target that is not provably disposable.

    ``test.dbname`` must come from ``SELECT current_database()`` on the live
    test connection, not from the configured URL.

    Raises:
        UnsafeTestDatabaseError: the name does not end in ``_test``, or the
            target is the application database.
    """
    if not test.dbname.endswith(TEST_DATABASE_SUFFIX):
        raise UnsafeTestDatabaseError(
            f"refusing to reset database {test.dbname!r}: integration tests "
            f"only reset a database whose name ends in {TEST_DATABASE_SUFFIX!r}"
        )
    if app_database_url and test == target_from_url(app_database_url):
        raise UnsafeTestDatabaseError(
            f"refusing to reset database {test.dbname!r}: it is the database "
            f"{APP_DATABASE_URL_ENV} points at"
        )


async def live_target(conn: Connection) -> DatabaseTarget:
    """The target a live connection actually reached."""
    cursor = await conn.execute("SELECT current_database()")
    row = await cursor.fetchone()
    if row is None:  # pragma: no cover - the query always returns one row
        raise UnsafeTestDatabaseError("could not read the current database name")
    return DatabaseTarget(
        host=_normalize_host(conn.info.host),
        port=str(conn.info.port),
        dbname=str(row[0]),
    )


async def reset_test_schema(conn: Connection) -> None:
    """Prove ``conn`` is disposable, then drop and re-apply the schema.

    Nothing destructive runs unless the guard passes. Dropping lives in the
    tests rather than the application: the migration itself only creates.
    """
    check_disposable(
        await live_target(conn), os.environ.get(APP_DATABASE_URL_ENV, "").strip()
    )
    await conn.execute("DROP TABLE IF EXISTS document_chunks, documents CASCADE")
    await apply_migration(conn)
    await conn.commit()


async def reset_test_database(url: str) -> None:
    """Open a plain connection to ``url`` and run ``reset_test_schema`` on it."""
    async with await psycopg.AsyncConnection.connect(url) as conn:
        await reset_test_schema(conn)
