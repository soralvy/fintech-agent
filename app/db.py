"""PostgreSQL access: pool lifecycle, pgvector registration, and repository functions.

This module owns SQL and nothing else. It makes no business decisions and must
never import FastAPI route objects, so ingestion and retrieval stay runnable in
tests without an HTTP server (docs/DECISIONS.md section 21).

All statements use bound parameters. Vectors bind through ``pgvector.Vector``;
every pooled connection registers the pgvector types in the pool's configure
hook (docs/DECISIONS.md section 6).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg import AsyncConnection
from psycopg.rows import TupleRow
from psycopg_pool import AsyncConnectionPool

from app.config import DatabaseConfig

type Connection = AsyncConnection[TupleRow]
type Pool = AsyncConnectionPool[Connection]

EMBEDDING_DIMENSIONS = 1536

MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent / "migrations" / "001_initial.sql"
)


@dataclass(frozen=True, slots=True)
class NewDocument:
    """A document row about to be written."""

    id: UUID
    filename: str
    content_type: str
    sha256: str
    page_count: int | None
    chunk_count: int


@dataclass(frozen=True, slots=True)
class NewChunk:
    """A chunk row about to be written, with its embedding."""

    id: UUID
    document_id: UUID
    chunk_index: int
    page_number: int | None
    content: str
    token_count: int | None
    embedding: Sequence[float]


@dataclass(frozen=True, slots=True)
class StoredDocument:
    """A document row read back from the database."""

    id: UUID
    filename: str
    content_type: str
    sha256: str
    page_count: int | None
    chunk_count: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChunkMatch:
    """One nearest-neighbour hit, carrying trusted metadata from the database.

    ``cosine_distance`` is left as pgvector returns it. Converting it to a
    similarity and filtering weak results belongs to the retrieval service, not
    here (docs/DECISIONS.md section 8).
    """

    chunk_id: UUID
    document_id: UUID
    filename: str
    page_number: int | None
    content: str
    cosine_distance: float


class EmbeddingDimensionError(ValueError):
    """An embedding did not have exactly ``EMBEDDING_DIMENSIONS`` dimensions."""


def _to_vector(embedding: Sequence[float]) -> Vector:
    """Convert an embedding to a bindable pgvector value.

    The dimension is checked here rather than left to PostgreSQL so a provider
    or configuration mistake is rejected before any row is written
    (docs/DECISIONS.md section 7.6).
    """
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise EmbeddingDimensionError(
            f"expected {EMBEDDING_DIMENSIONS} dimensions, got {len(embedding)}"
        )
    return Vector(embedding)


async def _configure_connection(conn: Connection) -> None:
    """Register the pgvector types on a freshly pooled connection."""
    await register_vector_async(conn)


def create_pool(config: DatabaseConfig) -> Pool:
    """Build the connection pool. The caller opens and closes it.

    ``timeout`` bounds every ``pool.connection()`` wait, so an unreachable
    database surfaces as ``PoolTimeout`` (a ``psycopg.Error``) within that bound
    instead of after psycopg_pool's 30-second default.

    The pool is opened without waiting for a first connection, deliberately:
    the application must start and answer ``/health`` with 503 while the
    database is down (docs/SPEC.md section 6.1), rather than refuse to start.
    """
    return AsyncConnectionPool(
        conninfo=config.url,
        min_size=config.min_pool_size,
        max_size=config.max_pool_size,
        timeout=config.pool_timeout_seconds,
        configure=_configure_connection,
        open=False,
    )


async def apply_migration(conn: Connection) -> None:
    """Apply ``migrations/001_initial.sql``.

    The SQL is idempotent, so this is safe to run against an existing database.
    """
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    # Encoded to bytes because psycopg types ``execute`` to accept only a
    # LiteralString or bytes, and this SQL is read from a file at runtime.
    await conn.execute(sql.encode("utf-8"))


async def check_database(pool: Pool) -> None:
    """Run the health query. Raises ``psycopg.Error`` if the database is unreachable."""
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")


async def insert_document(conn: Connection, document: NewDocument) -> None:
    """Insert one document row.

    Raises:
        psycopg.errors.UniqueViolation: a document with this checksum exists.
            The unique constraint, not the preliminary lookup, is the authority
            on duplicates (docs/DECISIONS.md section 6).
    """
    await conn.execute(
        """
        INSERT INTO documents
            (id, filename, content_type, sha256, page_count, chunk_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            document.id,
            document.filename,
            document.content_type,
            document.sha256,
            document.page_count,
            document.chunk_count,
        ),
    )


async def find_document_by_sha256(
    conn: Connection, sha256: str
) -> StoredDocument | None:
    """Look up a document by checksum, for duplicate detection."""
    cursor = await conn.execute(
        """
        SELECT id, filename, content_type, sha256, page_count, chunk_count, created_at
        FROM documents
        WHERE sha256 = %s
        """,
        (sha256,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return StoredDocument(
        id=row[0],
        filename=row[1],
        content_type=row[2],
        sha256=row[3],
        page_count=row[4],
        chunk_count=row[5],
        created_at=row[6],
    )


async def insert_chunks(conn: Connection, chunks: Sequence[NewChunk]) -> None:
    """Insert chunk rows and their embeddings.

    Does not open a transaction: the caller decides the boundary, because
    ingestion commits the document and all of its chunks together.
    """
    if not chunks:
        return
    async with conn.cursor() as cursor:
        await cursor.executemany(
            """
            INSERT INTO document_chunks
                (id, document_id, chunk_index, page_number, content, token_count, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    chunk.id,
                    chunk.document_id,
                    chunk.chunk_index,
                    chunk.page_number,
                    chunk.content,
                    chunk.token_count,
                    _to_vector(chunk.embedding),
                )
                for chunk in chunks
            ],
        )


async def search_chunks_by_embedding(
    conn: Connection, embedding: Sequence[float], limit: int
) -> list[ChunkMatch]:
    """Return the ``limit`` nearest chunks by exact cosine distance.

    Exact search, no ANN index: the corpus is deliberately small
    (docs/SPEC.md section 8.2).
    """
    query_vector = _to_vector(embedding)
    cursor = await conn.execute(
        """
        SELECT c.id,
               c.document_id,
               d.filename,
               c.page_number,
               c.content,
               c.embedding <=> %s AS cosine_distance
        FROM document_chunks AS c
        JOIN documents AS d ON d.id = c.document_id
        ORDER BY c.embedding <=> %s
        LIMIT %s
        """,
        (query_vector, query_vector, limit),
    )
    rows = await cursor.fetchall()
    return [
        ChunkMatch(
            chunk_id=row[0],
            document_id=row[1],
            filename=row[2],
            page_number=row[3],
            content=row[4],
            cosine_distance=float(row[5]),
        )
        for row in rows
    ]
