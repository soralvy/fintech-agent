"""Integration tests against a real PostgreSQL with pgvector.

These cover the Milestone 1 exit condition (chunks carrying vectors can be
inserted and the expected nearest vector retrieved) and the Milestone 3 one: a
known fixture question, embedded by a deterministic keyword fake, retrieves the
intended fixture chunk through the retrieval service.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.config import IngestionConfig, RetrievalConfig
from app.db import (
    SHA256_CONSTRAINT,
    Connection,
    DuplicateChecksumError,
    EmbeddingDimensionError,
    NewChunk,
    NewDocument,
    Pool,
    check_database,
    find_document_by_sha256,
    insert_chunks,
    insert_document,
    pooled_connection,
    pooled_transaction,
    search_chunks_by_embedding,
)
from app.errors import DatabaseUnavailableError
from app.ingestion import Ingestor
from app.logging import bind_request_id
from app.retrieval import RetrievedChunk, Retriever
from tests.conftest import embedding
from tests.fakes import FakeTokenizer, FakeUpload, KeywordEmbedder, build_pdf

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

    with pytest.raises(DuplicateChecksumError) as raised:
        await insert_document(db, make_document(sha256="c" * 64))

    assert raised.value.__cause__ is None and raised.value.__suppress_context__


async def test_checksum_constraint_carries_the_name_db_py_recognizes(
    db: Connection,
) -> None:
    cursor = await db.execute(
        """
        SELECT contype FROM pg_constraint
        WHERE conrelid = 'documents'::regclass AND conname = %s
        """,
        (SHA256_CONSTRAINT,),
    )

    assert await cursor.fetchone() == ("u",)


async def test_other_document_constraint_violations_are_not_duplicates(
    db: Connection,
) -> None:
    """Only the checksum constraint means "already ingested"."""
    document = make_document(sha256="7" * 64)
    await insert_document(db, document)
    same_id = NewDocument(
        id=document.id,
        filename="other.pdf",
        content_type="application/pdf",
        sha256="8" * 64,
        page_count=None,
        chunk_count=1,
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert_document(db, same_id)


async def test_transaction_turns_driver_errors_into_database_unavailable(
    pool: Pool, db: Connection
) -> None:
    """A non-checksum violation rolls back and surfaces without driver detail."""
    document = make_document(sha256="9" * 64)

    with pytest.raises(DatabaseUnavailableError) as raised:
        async with pooled_transaction(pool) as conn:
            await insert_document(conn, document)
            await insert_chunks(
                conn,
                [
                    make_chunk(document_id=document.id, chunk_index=0, hot_index=0),
                    make_chunk(document_id=document.id, chunk_index=0, hot_index=1),
                ],
            )

    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    await db.commit()
    assert await find_document_by_sha256(db, document.sha256) is None


async def test_transaction_lets_a_checksum_duplicate_through(
    pool: Pool, db: Connection
) -> None:
    await insert_document(db, make_document(sha256="6" * 64))
    await db.commit()

    with pytest.raises(DuplicateChecksumError):
        async with pooled_transaction(pool) as conn:
            await insert_document(conn, make_document(sha256="6" * 64))


async def test_pooled_connection_turns_driver_errors_into_database_unavailable(
    pool: Pool, db: Connection
) -> None:
    with pytest.raises(DatabaseUnavailableError):
        async with pooled_connection(pool) as conn:
            await conn.execute("SELECT * FROM no_such_table")


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
    first at distance 0.0, then the two tied at 1.0 in ``chunk_index`` order.
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


async def test_equal_distances_are_ordered_by_document_then_chunk_index(
    db: Connection,
) -> None:
    """Ties are common (the fakes use orthogonal one-hot vectors), so the order
    among equally distant chunks must be defined, not left to the scan."""
    documents = [make_document(sha256=char * 64, chunk_count=3) for char in "ab"]
    for document in documents:
        await insert_document(db, document)
    # Inserted out of order; every chunk is orthogonal to the query.
    for document in reversed(documents):
        await insert_chunks(
            db,
            [
                make_chunk(
                    document_id=document.id,
                    chunk_index=index,
                    hot_index=10 + index,
                    content=f"{document.sha256[0]}{index}",
                )
                for index in (2, 0, 1)
            ],
        )

    matches = await search_chunks_by_embedding(db, embedding(hot_index=0), limit=6)

    assert len(matches) == 6
    assert all(match.cosine_distance == pytest.approx(1.0) for match in matches)
    first, second = sorted(documents, key=lambda document: document.id)
    assert [match.content for match in matches] == [
        f"{document.sha256[0]}{index}"
        for document in (first, second)
        for index in (0, 1, 2)
    ]


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


async def test_no_ann_index_exists_on_document_chunks(db: Connection) -> None:
    """Exact search only (docs/DECISIONS.md section 22): every index on the
    chunk table is a B-tree, none is HNSW or IVFFlat."""
    cursor = await db.execute(
        """
        SELECT am.amname
        FROM pg_index AS i
        JOIN pg_class AS c ON c.oid = i.indexrelid
        JOIN pg_am AS am ON am.oid = c.relam
        WHERE i.indrelid = 'document_chunks'::regclass
        """
    )
    access_methods = {row[0] for row in await cursor.fetchall()}

    assert access_methods == {"btree"}


# ---------------------------------------------------------------------------
# Retrieval service against real pgvector (Milestone 3)
# ---------------------------------------------------------------------------

SMOKE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "smoke.txt"

LIQUIDITY_MD = (
    "# Acme liquidity\n\n"
    "Acme ended the fiscal year with cash and equivalents of 1.2 billion dollars "
    "and an undrawn revolving credit facility."
)
GLOBEX_PAGES = [
    "Globex Holdings annual report. Letter from the chief executive to shareholders.",
    (
        "Globex operating margin expanded to 18 percent, driven by pricing actions "
        "in North America."
    ),
]


@dataclass(frozen=True)
class Corpus:
    acme_report: UUID
    acme_liquidity: UUID
    globex_report: UUID


async def ingest_corpus(pool: Pool) -> Corpus:
    """Ingest the fixture corpus through the real ingestion path."""
    ingestor = Ingestor(
        pool=pool,
        embedder=KeywordEmbedder(),
        tokenizer=FakeTokenizer(),
        config=IngestionConfig(),
    )
    uploads = [
        FakeUpload("acme-fy2025.txt", "text/plain", SMOKE_FIXTURE.read_bytes()),
        FakeUpload("acme-liquidity.md", "text/markdown", LIQUIDITY_MD.encode()),
        FakeUpload("globex-2025.pdf", "application/pdf", build_pdf(GLOBEX_PAGES)),
    ]
    ids = [(await ingestor.ingest(u, request_id="seed")).document_id for u in uploads]
    return Corpus(*ids)


def make_retriever(
    pool: Pool, *, top_k: int = 6, min_similarity: float = 0.30
) -> Retriever:
    return Retriever(
        pool=pool,
        embedder=KeywordEmbedder(),
        config=RetrievalConfig(top_k=top_k, min_similarity=min_similarity),
    )


async def ask(retriever: Retriever, question: str) -> list[RetrievedChunk]:
    return await retriever.retrieve(await retriever.embed_query(question))


async def test_fixture_question_retrieves_the_intended_chunk(
    pool: Pool, db: Connection
) -> None:
    """The Milestone 3 exit condition, with the default threshold of 0.30."""
    corpus = await ingest_corpus(pool)

    chunks = await ask(make_retriever(pool), "Why did Acme's European revenue decline?")

    assert len(chunks) == 1, "the related liquidity chunk scores below 0.30"
    (chunk,) = chunks
    assert chunk.document_id == corpus.acme_report
    assert chunk.filename == "acme-fy2025.txt"
    assert chunk.page_number is None
    assert "European revenue declined 4%" in chunk.content
    assert chunk.similarity == pytest.approx(1 - chunk.cosine_distance)
    assert chunk.similarity >= 0.30
    await db.commit()
    stored = await (
        await db.execute(
            "SELECT document_id, content FROM document_chunks WHERE id = %s",
            (chunk.chunk_id,),
        )
    ).fetchone()
    assert stored == (chunk.document_id, chunk.content)


async def test_pdf_chunk_carries_its_page_number(pool: Pool, db: Connection) -> None:
    corpus = await ingest_corpus(pool)

    (chunk,) = await ask(
        make_retriever(pool), "What happened to Globex operating margin?"
    )

    assert chunk.document_id == corpus.globex_report
    assert chunk.filename == "globex-2025.pdf"
    assert chunk.page_number == 2


async def test_lower_threshold_admits_weaker_chunks_in_similarity_order(
    pool: Pool, db: Connection
) -> None:
    corpus = await ingest_corpus(pool)

    chunks = await ask(
        make_retriever(pool, min_similarity=0.1),
        "Why did Acme's European revenue decline?",
    )

    assert [chunk.document_id for chunk in chunks] == [
        corpus.acme_report,
        corpus.acme_liquidity,
    ]
    assert chunks[0].similarity > chunks[1].similarity >= 0.1


async def test_unrelated_question_retrieves_nothing(pool: Pool, db: Connection) -> None:
    """Candidates always exist; weak ones must not count as evidence."""
    await ingest_corpus(pool)

    assert await ask(make_retriever(pool), "What is Initech's dividend policy?") == []


async def test_zero_query_vector_is_rejected_as_non_finite(
    pool: Pool, db: Connection
) -> None:
    """pgvector returns NaN cosine distance for a zero-norm vector; even a
    threshold of 0 must not accept it."""
    await ingest_corpus(pool)

    assert await ask(make_retriever(pool, min_similarity=0.0), "What was it?") == []


async def test_top_k_bounds_the_candidates(pool: Pool, db: Connection) -> None:
    await ingest_corpus(pool)

    chunks = await ask(
        make_retriever(pool, top_k=2, min_similarity=0.0), "Acme Globex revenue"
    )

    assert len(chunks) == 2


async def test_empty_corpus_retrieves_nothing(pool: Pool, db: Connection) -> None:
    assert await ask(make_retriever(pool), "Why did revenue decline?") == []


async def test_retrieval_events_carry_counts_but_no_content(
    pool: Pool, db: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    await ingest_corpus(pool)
    caplog.set_level(logging.DEBUG)
    retriever = make_retriever(pool)

    with bind_request_id("req-retrieve"):
        await ask(retriever, "Why did Acme's European revenue decline?")

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.retrieval"
    ]
    assert [event["event"] for event in events] == [
        "retrieval.started",
        "retrieval.completed",
    ]
    completed = events[1]
    assert completed["request_id"] == "req-retrieve"
    assert (completed["top_k"], completed["minimum_similarity"]) == (6, 0.30)
    assert (completed["candidate_count"], completed["accepted_count"]) == (4, 1)
    assert completed["top_similarity"] >= 0.30
    assert "duration_ms" in completed
    everything = "\n".join(record.getMessage() for record in caplog.records)
    assert "currency headwinds" not in everything
    assert "European revenue" not in everything
