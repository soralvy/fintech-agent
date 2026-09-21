"""Integration tests against a real PostgreSQL with pgvector.

These cover the Milestone 1 exit condition: chunks carrying vectors can be
inserted and the expected nearest vector retrieved.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from app.db import (
    Connection,
    EmbeddingDimensionError,
    NewChunk,
    NewDocument,
    Pool,
    check_database,
    find_document_by_sha256,
    insert_chunks,
    insert_document,
    search_chunks_by_embedding,
)
from tests.conftest import embedding

pytestmark = pytest.mark.anyio


def make_document(*, sha256: str, chunk_count: int = 1) -> NewDocument:
    return NewDocument(
        id=uuid4(),
        filename="acme-annual-report.pdf",
        content_type="application/pdf",
        sha256=sha256,
        page_count=18,
        chunk_count=chunk_count,
    )


def make_chunk(
    *,
    document_id: UUID,
    chunk_index: int,
    hot_index: int,
    content: str = "revenue grew",
) -> NewChunk:
    return NewChunk(
        id=uuid4(),
        document_id=document_id,
        chunk_index=chunk_index,
        page_number=1,
        content=content,
        token_count=3,
        embedding=embedding(hot_index=hot_index),
    )


async def test_vector_extension_is_enabled(db: Connection) -> None:
    cursor = await db.execute(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
    )
    row = await cursor.fetchone()

    assert row is not None, "the vector extension is not installed"


async def test_embedding_column_is_1536_dimensional(db: Connection) -> None:
    cursor = await db.execute(
        """
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute AS a
        WHERE a.attrelid = 'document_chunks'::regclass AND a.attname = 'embedding'
        """
    )
    row = await cursor.fetchone()

    assert row is not None
    assert row[0] == "vector(1536)"


async def test_migration_is_idempotent(db: Connection) -> None:
    from app.db import apply_migration

    await apply_migration(db)

    cursor = await db.execute("SELECT count(*) FROM documents")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_insert_document_then_find_by_checksum(db: Connection) -> None:
    document = make_document(sha256="a" * 64)

    await insert_document(db, document)
    found = await find_document_by_sha256(db, document.sha256)

    assert found is not None
    assert found.id == document.id
    assert found.filename == document.filename
    assert found.page_count == 18


async def test_find_by_checksum_returns_none_when_absent(db: Connection) -> None:
    assert await find_document_by_sha256(db, "b" * 64) is None


async def test_duplicate_checksum_is_rejected(db: Connection) -> None:
    await insert_document(db, make_document(sha256="c" * 64))

    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert_document(db, make_document(sha256="c" * 64))


async def test_chunk_index_is_unique_per_document(db: Connection) -> None:
    document = make_document(sha256="d" * 64)
    await insert_document(db, document)
    await insert_chunks(
        db, [make_chunk(document_id=document.id, chunk_index=0, hot_index=0)]
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert_chunks(
            db, [make_chunk(document_id=document.id, chunk_index=0, hot_index=1)]
        )


async def test_chunk_requires_an_existing_document(db: Connection) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await insert_chunks(
            db, [make_chunk(document_id=uuid4(), chunk_index=0, hot_index=0)]
        )


async def test_deleting_a_document_cascades_to_its_chunks(db: Connection) -> None:
    document = make_document(sha256="e" * 64)
    await insert_document(db, document)
    await insert_chunks(
        db, [make_chunk(document_id=document.id, chunk_index=0, hot_index=0)]
    )

    await db.execute("DELETE FROM documents WHERE id = %s", (document.id,))

    cursor = await db.execute("SELECT count(*) FROM document_chunks")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_blank_chunk_content_is_rejected(db: Connection) -> None:
    document = make_document(sha256="f" * 64)
    await insert_document(db, document)

    with pytest.raises(psycopg.errors.CheckViolation):
        await insert_chunks(
            db,
            [
                make_chunk(
                    document_id=document.id, chunk_index=0, hot_index=0, content="   "
                )
            ],
        )


async def test_failed_transaction_leaves_no_rows(db: Connection) -> None:
    """A rolled-back transaction must not leave the document behind."""
    document = make_document(sha256="1" * 64)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        async with db.transaction():
            await insert_document(db, document)
            await insert_chunks(
                db, [make_chunk(document_id=uuid4(), chunk_index=0, hot_index=0)]
            )

    assert await find_document_by_sha256(db, document.sha256) is None


async def test_nearest_vector_is_retrieved_by_cosine_distance(db: Connection) -> None:
    """The Milestone 1 exit condition.

    Three orthogonal chunk vectors; a query equal to the second must come back
    first at distance 0.0, with the rest at 1.0.
    """
    document = make_document(sha256="2" * 64, chunk_count=3)
    await insert_document(db, document)
    await insert_chunks(
        db,
        [
            make_chunk(
                document_id=document.id, chunk_index=0, hot_index=0, content="alpha"
            ),
            make_chunk(
                document_id=document.id, chunk_index=1, hot_index=1, content="bravo"
            ),
            make_chunk(
                document_id=document.id, chunk_index=2, hot_index=2, content="charlie"
            ),
        ],
    )

    matches = await search_chunks_by_embedding(db, embedding(hot_index=1), limit=3)

    assert [match.content for match in matches] == ["bravo", "alpha", "charlie"]
    assert matches[0].cosine_distance == pytest.approx(0.0)
    assert matches[1].cosine_distance == pytest.approx(1.0)
    assert matches[2].cosine_distance == pytest.approx(1.0)


async def test_match_carries_trusted_document_metadata(db: Connection) -> None:
    """Retrieval returns the metadata citations are later built from."""
    document = make_document(sha256="3" * 64)
    await insert_document(db, document)
    await insert_chunks(
        db, [make_chunk(document_id=document.id, chunk_index=0, hot_index=0)]
    )

    (match,) = await search_chunks_by_embedding(db, embedding(hot_index=0), limit=6)

    assert match.document_id == document.id
    assert match.filename == "acme-annual-report.pdf"
    assert match.page_number == 1
    assert match.content == "revenue grew"


async def test_search_limit_is_honoured(db: Connection) -> None:
    document = make_document(sha256="4" * 64, chunk_count=3)
    await insert_document(db, document)
    await insert_chunks(
        db,
        [
            make_chunk(document_id=document.id, chunk_index=index, hot_index=index)
            for index in range(3)
        ],
    )

    matches = await search_chunks_by_embedding(db, embedding(hot_index=0), limit=2)

    assert len(matches) == 2


async def test_search_on_empty_corpus_returns_nothing(db: Connection) -> None:
    assert await search_chunks_by_embedding(db, embedding(hot_index=0), limit=6) == []


async def test_wrong_dimension_embedding_is_rejected_before_insert(
    db: Connection,
) -> None:
    """A dimension mismatch aborts before any row is written."""
    document = make_document(sha256="5" * 64)
    await insert_document(db, document)
    chunk = NewChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=0,
        page_number=None,
        content="short vector",
        token_count=2,
        embedding=[1.0, 0.0, 0.0],
    )

    with pytest.raises(EmbeddingDimensionError):
        await insert_chunks(db, [chunk])

    cursor = await db.execute("SELECT count(*) FROM document_chunks")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == 0


async def test_check_database_succeeds_through_a_real_pooled_connection(
    pool: Pool,
) -> None:
    """The health query against real PostgreSQL, not the HTTP tests' fake."""
    await check_database(pool)
