"""Ingestion tests: pure validation, extraction, and chunking, then the service
against a real PostgreSQL with pgvector.

External services are always fakes (docs/DECISIONS.md section 20): the
tokenizer counts code points and the embedder returns one-hot vectors.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import time
from dataclasses import replace
from typing import Any, NoReturn, cast
from uuid import uuid4

import psycopg
import pytest
from pypdf import PdfReader, PdfWriter

from app.config import IngestionConfig
from app.db import (
    Connection,
    DuplicateChecksumError,
    NewChunk,
    NewDocument,
    Pool,
    insert_chunks,
)
from app.errors import (
    AppError,
    DatabaseUnavailableError,
    EmbeddingProviderError,
    EmptyDocumentError,
    TokenizerUnavailableError,
    UnparseableDocumentError,
    UnsupportedFileTypeError,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
)
from app.ingestion import (
    ChunkDraft,
    Ingestor,
    PageText,
    build_chunks,
    extract_document,
    normalize_filename,
    normalize_whitespace,
    read_bounded,
    resolve_file_kind,
)
from app.tokenizer import TiktokenTokenizer, Tokenizer
from tests.fakes import FakeEmbedder, FakeTokenizer, FakeUpload, build_pdf

# ---------------------------------------------------------------------------
# File type and media type validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "media_type", "content_type"),
    [
        ("report.pdf", "application/pdf", "application/pdf"),
        ("report.PDF", "application/octet-stream", "application/pdf"),
        ("notes.txt", "text/plain", "text/plain"),
        ("notes.txt", "text/plain; charset=utf-8", "text/plain"),
        ("notes.txt", "application/octet-stream", "text/plain"),
        ("notes.md", "text/markdown", "text/markdown"),
        ("notes.md", "text/x-markdown", "text/markdown"),
        ("notes.md", "text/plain", "text/markdown"),
        ("notes.markdown", "application/octet-stream", "text/markdown"),
    ],
)
def test_supported_extension_and_media_type_are_accepted(
    filename: str, media_type: str, content_type: str
) -> None:
    assert resolve_file_kind(filename, media_type).content_type == content_type


@pytest.mark.parametrize(
    "filename", ["report.docx", "report.exe", "report", "report.", "", "pdf"]
)
def test_unsupported_extension_is_rejected(filename: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        resolve_file_kind(filename, "application/octet-stream")


@pytest.mark.parametrize(
    ("filename", "media_type"),
    [
        ("report.pdf", "text/plain"),
        ("report.pdf", "text/markdown"),
        ("notes.txt", "application/pdf"),
        ("notes.txt", "text/markdown"),
        ("notes.md", "application/pdf"),
        ("report.pdf", None),
        ("notes.txt", ""),
        ("notes.md", "text/html"),
    ],
)
def test_media_type_outside_the_extension_allow_list_is_rejected(
    filename: str, media_type: str | None
) -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        resolve_file_kind(filename, media_type)


def test_octet_stream_does_not_make_an_unsupported_extension_acceptable() -> None:
    with pytest.raises(UnsupportedFileTypeError):
        resolve_file_kind("payload.exe", "application/octet-stream")


def test_filename_keeps_only_the_final_path_component() -> None:
    assert normalize_filename("../../etc/report.pdf") == "report.pdf"
    assert normalize_filename("C:\\Users\\me\\report.pdf") == "report.pdf"
    assert normalize_filename(None) == ""


def test_filename_nul_bytes_are_stripped() -> None:
    """A NUL in the filename would otherwise reach PostgreSQL and fail as a
    misleading database error, after a paid embedding call."""
    assert normalize_filename("a\x00b.txt") == "ab.txt"


# ---------------------------------------------------------------------------
# Bounded reading
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_upload_at_the_limit_is_read_whole() -> None:
    upload = FakeUpload("a.txt", "text/plain", b"x" * 100)

    assert await read_bounded(upload, 100) == b"x" * 100


@pytest.mark.anyio
async def test_upload_over_the_limit_stops_after_limit_plus_one_byte() -> None:
    upload = FakeUpload("a.txt", "text/plain", b"x" * 10_000_000)

    with pytest.raises(UploadTooLargeError):
        await read_bounded(upload, 100)

    assert upload.bytes_read == 101


# ---------------------------------------------------------------------------
# Extraction and normalization
# ---------------------------------------------------------------------------


def test_whitespace_normalization_is_structural_only() -> None:
    raw = "  Revenue\tgrew   12%\r\nin  Europe.  \r\n\r\n\r\n\r\nRisks\x00 remain.  "

    assert normalize_whitespace(raw) == "Revenue grew 12%\nin Europe.\n\nRisks remain."


def test_text_is_decoded_as_strict_utf8() -> None:
    kind = resolve_file_kind("notes.txt", "text/plain")

    extracted = extract_document(kind, "Umsatz stieg um 5 % — Europa".encode())

    assert extracted.pages == [PageText(None, "Umsatz stieg um 5 % — Europa")]
    assert extracted.page_count is None


def test_a_leading_utf8_byte_order_mark_is_dropped() -> None:
    kind = resolve_file_kind("notes.txt", "text/plain")

    extracted = extract_document(kind, b"\xef\xbb\xbfRevenue grew.")

    assert extracted.pages == [PageText(None, "Revenue grew.")]


def test_invalid_utf8_is_rejected() -> None:
    kind = resolve_file_kind("notes.md", "text/markdown")

    with pytest.raises(UnparseableDocumentError):
        extract_document(kind, b"valid start \xff\xfe invalid")


def test_whitespace_only_text_yields_no_pages() -> None:
    kind = resolve_file_kind("notes.txt", "text/plain")

    assert extract_document(kind, b" \n\t\r\n ").pages == []


def test_pdf_pages_keep_one_based_numbers_and_skip_empty_pages() -> None:
    kind = resolve_file_kind("report.pdf", "application/pdf")
    data = build_pdf(["First page text", None, "Third page text"])

    extracted = extract_document(kind, data)

    assert extracted.page_count == 3
    assert extracted.pages == [
        PageText(1, "First page text"),
        PageText(3, "Third page text"),
    ]


def test_pdf_without_signature_is_rejected_before_parsing() -> None:
    kind = resolve_file_kind("report.pdf", "application/pdf")

    with pytest.raises(UnparseableDocumentError):
        extract_document(kind, b"MZ\x90\x00 not a pdf at all")


def test_malformed_pdf_is_rejected() -> None:
    kind = resolve_file_kind("report.pdf", "application/pdf")

    with pytest.raises(UnparseableDocumentError):
        extract_document(kind, b"%PDF-1.4\n garbage without objects or xref")


def test_encrypted_pdf_is_rejected() -> None:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(build_pdf(["secret"]))))
    writer.encrypt("owner-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    kind = resolve_file_kind("report.pdf", "application/pdf")

    with pytest.raises(UnparseableDocumentError):
        extract_document(kind, buffer.getvalue())


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _chunks(
    pages: list[PageText], tokenizer: Tokenizer | None = None
) -> list[ChunkDraft]:
    return build_chunks(
        pages,
        tokenizer or FakeTokenizer(),
        chunk_tokens=800,
        overlap_tokens=120,
    )


def test_windows_are_800_tokens_advancing_by_680() -> None:
    text = "".join(chr(ord("a") + i % 26) for i in range(2000))

    chunks = _chunks([PageText(None, text)])

    assert [(c.content, c.token_count) for c in chunks] == [
        (text[0:800], 800),
        (text[680:1480], 800),
        (text[1360:2000], 640),
    ]
    assert chunks[0].content[-120:] == chunks[1].content[:120]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]


def test_text_that_fits_one_window_is_one_chunk() -> None:
    assert len(_chunks([PageText(None, "x" * 800)])) == 1
    assert len(_chunks([PageText(None, "x" * 801)])) == 2


def test_chunking_is_deterministic() -> None:
    pages = [PageText(1, "alpha " * 300), PageText(2, "beta " * 400)]

    assert _chunks(pages) == _chunks(pages)


def test_chunks_never_cross_pdf_pages_and_indexes_run_across_the_document() -> None:
    page_one = "a" * 1000
    page_two = "b" * 300

    chunks = _chunks([PageText(1, page_one), PageText(3, page_two)])

    assert [(c.chunk_index, c.page_number) for c in chunks] == [(0, 1), (1, 1), (2, 3)]
    assert all(set(c.content) == {"a"} for c in chunks if c.page_number == 1)
    assert chunks[2].content == page_two


def test_blank_decoded_windows_are_discarded() -> None:
    class BlankTailTokenizer(FakeTokenizer):
        def decode(self, tokens: Any) -> str:
            return "" if tokens and tokens[0] == ord("z") else super().decode(tokens)

    chunks = _chunks([PageText(None, "a" * 700 + "z" * 700)], BlankTailTokenizer())

    assert [c.chunk_index for c in chunks] == [0, 1]


# ---------------------------------------------------------------------------
# Service against PostgreSQL
# ---------------------------------------------------------------------------

REQUEST_ID = "test-request"


def make_ingestor(
    pool: Pool,
    embedder: FakeEmbedder | None = None,
    tokenizer: Tokenizer | None = None,
    max_upload_bytes: int = 1_000_000,
) -> Ingestor:
    return Ingestor(
        pool=pool,
        embedder=embedder or FakeEmbedder(),
        tokenizer=tokenizer or FakeTokenizer(),
        config=IngestionConfig(max_upload_bytes=max_upload_bytes),
    )


async def count_rows(db: Connection) -> tuple[int, int]:
    await db.commit()
    documents = await (await db.execute("SELECT count(*) FROM documents")).fetchone()
    chunks = await (await db.execute("SELECT count(*) FROM document_chunks")).fetchone()
    await db.commit()
    assert documents is not None and chunks is not None
    return documents[0], chunks[0]


@pytest.mark.anyio
async def test_text_document_is_persisted_with_its_chunks(
    pool: Pool, db: Connection
) -> None:
    data = ("Acme revenue in Europe grew. " * 60).encode()
    embedder = FakeEmbedder()

    result = await make_ingestor(pool, embedder).ingest(
        FakeUpload("acme.txt", "text/plain", data), request_id=REQUEST_ID
    )

    assert result.status == "ingested"
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.page_count is None
    assert result.chunk_count == 3
    assert len(embedder.calls) == 1 and len(embedder.calls[0]) == 3
    cursor = await db.execute(
        "SELECT filename, content_type, chunk_count FROM documents WHERE id = %s",
        (result.document_id,),
    )
    assert await cursor.fetchone() == ("acme.txt", "text/plain", 3)
    cursor = await db.execute(
        """
        SELECT chunk_index, page_number, token_count, vector_dims(embedding)
        FROM document_chunks WHERE document_id = %s ORDER BY chunk_index
        """,
        (result.document_id,),
    )
    rows = await cursor.fetchall()
    assert [row[0] for row in rows] == [0, 1, 2]
    assert all(row[1] is None and row[3] == 1536 for row in rows)
    assert [row[2] for row in rows] == [800, 800, len(data.strip()) - 1360]


@pytest.mark.anyio
async def test_markdown_document_is_stored_with_canonical_content_type(
    pool: Pool, db: Connection
) -> None:
    result = await make_ingestor(pool).ingest(
        FakeUpload("notes.md", "text/plain", b"# Risks\n\nFX exposure."),
        request_id=REQUEST_ID,
    )

    cursor = await db.execute(
        "SELECT content_type FROM documents WHERE id = %s", (result.document_id,)
    )
    assert await cursor.fetchone() == ("text/markdown",)
    cursor = await db.execute("SELECT content FROM document_chunks")
    assert await cursor.fetchall() == [("# Risks\n\nFX exposure.",)]


@pytest.mark.anyio
async def test_multipage_pdf_chunks_carry_one_based_page_numbers(
    pool: Pool, db: Connection
) -> None:
    data = build_pdf(["Page one revenue", None, "Page three risks"])

    result = await make_ingestor(pool).ingest(
        FakeUpload("report.pdf", "application/octet-stream", data),
        request_id=REQUEST_ID,
    )

    assert result.page_count == 3
    assert result.chunk_count == 2
    cursor = await db.execute(
        "SELECT page_number, content FROM document_chunks ORDER BY chunk_index"
    )
    assert await cursor.fetchall() == [(1, "Page one revenue"), (3, "Page three risks")]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("filename", "media_type", "data", "error"),
    [
        pytest.param("empty.txt", "text/plain", b"", EmptyDocumentError, id="empty"),
        pytest.param(
            "blank.md", "text/markdown", b"  \n\n\t ", EmptyDocumentError, id="blank"
        ),
        pytest.param(
            "blank.pdf",
            "application/pdf",
            build_pdf([None, None]),
            EmptyDocumentError,
            id="pdf-without-text",
        ),
        pytest.param(
            "bad.txt", "text/plain", b"\xff\xfe", UnparseableDocumentError, id="utf8"
        ),
        pytest.param(
            "fake.pdf",
            "application/pdf",
            b"plain text",
            UnparseableDocumentError,
            id="pdf-signature",
        ),
        pytest.param(
            "run.exe",
            "application/octet-stream",
            b"MZ",
            UnsupportedFileTypeError,
            id="extension",
        ),
        pytest.param(
            "a.pdf",
            "text/plain",
            build_pdf(["x"]),
            UnsupportedMediaTypeError,
            id="mime-mismatch",
        ),
        pytest.param(
            "big.txt", "text/plain", b"x" * 2001, UploadTooLargeError, id="too-large"
        ),
    ],
)
async def test_rejected_uploads_are_never_embedded_or_stored(
    pool: Pool,
    db: Connection,
    filename: str,
    media_type: str,
    data: bytes,
    error: type[AppError],
) -> None:
    embedder = FakeEmbedder()

    with pytest.raises(error):
        await make_ingestor(pool, embedder, max_upload_bytes=2000).ingest(
            FakeUpload(filename, media_type, data), request_id=REQUEST_ID
        )

    assert embedder.calls == []
    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_duplicate_is_detected_before_extraction_and_embedding(
    pool: Pool, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"Quarterly revenue rose."
    first = await make_ingestor(pool).ingest(
        FakeUpload("q.txt", "text/plain", data), request_id=REQUEST_ID
    )

    def extraction_must_not_run(*args: object) -> None:
        raise AssertionError("a duplicate was re-extracted")

    monkeypatch.setattr("app.ingestion.extract_document", extraction_must_not_run)
    embedder = FakeEmbedder()
    second = await make_ingestor(pool, embedder).ingest(
        FakeUpload("renamed.txt", "text/plain", data), request_id=REQUEST_ID
    )

    assert second.status == "already_ingested"
    assert second.document_id == first.document_id
    assert second.filename == "q.txt"
    assert embedder.calls == []
    assert await count_rows(db) == (1, first.chunk_count)


@pytest.mark.anyio
async def test_concurrent_duplicate_uploads_store_one_document(
    pool: Pool, db: Connection
) -> None:
    """Both requests pass the duplicate lookup; the unique constraint decides."""
    data = ("Concurrent upload of identical bytes. " * 40).encode()
    embedder = FakeEmbedder(barrier=asyncio.Barrier(2))
    ingestor = make_ingestor(pool, embedder)

    results = await asyncio.gather(
        ingestor.ingest(FakeUpload("a.txt", "text/plain", data), request_id="r1"),
        ingestor.ingest(FakeUpload("b.txt", "text/plain", data), request_id="r2"),
    )

    assert len(embedder.calls) == 2, "both requests should have reached embedding"
    assert sorted(r.status for r in results) == ["already_ingested", "ingested"]
    assert results[0].document_id == results[1].document_id
    assert await count_rows(db) == (1, results[0].chunk_count)


@pytest.mark.anyio
async def test_embedding_failure_leaves_no_rows(pool: Pool, db: Connection) -> None:
    embedder = FakeEmbedder(error=EmbeddingProviderError())

    with pytest.raises(EmbeddingProviderError):
        await make_ingestor(pool, embedder).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_wrong_dimension_embeddings_abort_before_any_write(
    pool: Pool, db: Connection
) -> None:
    embedder = FakeEmbedder(dimensions=8)

    with pytest.raises(EmbeddingProviderError):
        await make_ingestor(pool, embedder).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_chunk_write_failure_rolls_back_the_document_row(
    pool: Pool, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The document row is written first; a chunk failure must take it back out."""

    async def failing_insert_chunks(*args: object) -> None:
        raise psycopg.errors.DataError("password=hunter2 host=db.internal")

    monkeypatch.setattr("app.ingestion.insert_chunks", failing_insert_chunks)

    with pytest.raises(DatabaseUnavailableError):
        await make_ingestor(pool).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_another_unique_violation_is_503_not_a_duplicate(
    pool: Pool, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the checksum constraint means "already ingested"; a real violation
    of the chunk-index constraint is a database failure and rolls back."""

    async def insert_with_repeated_index(
        conn: Connection, chunks: list[NewChunk]
    ) -> None:
        await insert_chunks(conn, [*chunks, replace(chunks[0], id=uuid4())])

    monkeypatch.setattr("app.ingestion.insert_chunks", insert_with_repeated_index)

    with pytest.raises(DatabaseUnavailableError) as raised:
        await make_ingestor(pool).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_checksum_conflict_without_a_readable_winner_is_503(
    pool: Pool, db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The constraint reported a duplicate, but no winner can be read back
    (for example it was deleted in between): never report a phantom document."""

    async def conflicting_insert(conn: Connection, document: NewDocument) -> None:
        raise DuplicateChecksumError

    monkeypatch.setattr("app.ingestion.insert_document", conflicting_insert)
    embedder = FakeEmbedder()

    with pytest.raises(DatabaseUnavailableError):
        await make_ingestor(pool, embedder).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert len(embedder.calls) == 1
    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_nul_characters_are_stripped_so_postgres_accepts_the_text(
    pool: Pool, db: Connection
) -> None:
    await make_ingestor(pool).ingest(
        FakeUpload("a.txt", "text/plain", b"Net\x00 income"), request_id=REQUEST_ID
    )

    cursor = await db.execute("SELECT content FROM document_chunks")
    assert await cursor.fetchall() == [("Net income",)]


@pytest.mark.anyio
async def test_tokenizer_failure_is_503_and_leaves_no_rows(
    pool: Pool, db: Connection
) -> None:
    class UnavailableTokenizer(FakeTokenizer):
        def encode(self, text: str) -> list[int]:
            raise TokenizerUnavailableError

    embedder = FakeEmbedder()

    with pytest.raises(TokenizerUnavailableError):
        await make_ingestor(pool, embedder, UnavailableTokenizer()).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert embedder.calls == []
    assert await count_rows(db) == (0, 0)


@pytest.mark.anyio
async def test_slow_tokenizer_load_times_out_without_blocking_the_request(
    pool: Pool, db: Connection
) -> None:
    """tiktoken's own loader has no HTTP timeout; ``ensure_ready`` must bound
    it so a stalled download cannot hang the whole request."""

    def slow_loader(name: str) -> NoReturn:
        time.sleep(0.2)
        raise AssertionError("must not be awaited long enough to return")

    tokenizer = TiktokenTokenizer(loader=slow_loader, timeout_seconds=0.02)
    embedder = FakeEmbedder()
    started = time.monotonic()

    with pytest.raises(TokenizerUnavailableError):
        await make_ingestor(pool, embedder, tokenizer).ingest(
            FakeUpload("a.txt", "text/plain", b"Some text."), request_id=REQUEST_ID
        )

    assert time.monotonic() - started < 0.2, (
        "the deadline, not the slow loader, must decide"
    )
    assert embedder.calls == []
    assert await count_rows(db) == (0, 0)


class _BrokenConnection:
    async def __aenter__(self) -> None:
        raise psycopg.OperationalError("connection to postgresql://u:hunter2@db failed")

    async def __aexit__(self, *args: object) -> None:
        return None


class _UnreachablePool:
    def connection(self) -> _BrokenConnection:
        return _BrokenConnection()


@pytest.mark.anyio
async def test_unreachable_database_is_reported_without_driver_details() -> None:
    ingestor = Ingestor(
        pool=cast(Pool, _UnreachablePool()),
        embedder=FakeEmbedder(),
        tokenizer=FakeTokenizer(),
        config=IngestionConfig(),
    )

    with pytest.raises(DatabaseUnavailableError) as raised:
        await ingestor.ingest(
            FakeUpload("a.txt", "text/plain", b"text"), request_id=REQUEST_ID
        )

    assert "hunter2" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name.startswith("app.")
    ]


@pytest.mark.anyio
async def test_logs_carry_safe_fields_only(
    pool: Pool, db: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    secret_text = "CONFIDENTIAL-MARKER quarterly guidance"
    data = (secret_text + " ").encode() * 50
    sha256 = hashlib.sha256(data).hexdigest()
    caplog.set_level(logging.DEBUG)
    ingestor = make_ingestor(pool)

    await ingestor.ingest(FakeUpload("a.txt", "text/plain", data), request_id="r1")
    await ingestor.ingest(FakeUpload("a.txt", "text/plain", data), request_id="r2")
    with pytest.raises(UnparseableDocumentError):
        await ingestor.ingest(
            FakeUpload("b.txt", "text/plain", b"\xff" + secret_text.encode()),
            request_id="r3",
        )

    events = _events(caplog)
    names = [event["event"] for event in events]
    assert names == [
        "ingestion.started",
        "ingestion.parsed",
        "ingestion.embedded",
        "ingestion.persisted",
        "ingestion.started",
        "ingestion.duplicate",
        "ingestion.started",
        "ingestion.failed",
    ]
    assert events[-1]["error_code"] == "unparseable_document"
    assert events[3]["sha256_prefix"] == sha256[:12]
    everything = "\n".join(record.getMessage() for record in caplog.records)
    assert "CONFIDENTIAL-MARKER" not in everything
    assert sha256 not in everything
    for record in caplog.records:
        fields: dict[str, object] = getattr(record, "event_fields", {})
        assert all(not isinstance(v, list | dict | bytes) for v in fields.values())
