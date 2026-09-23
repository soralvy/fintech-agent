"""Synchronous document ingestion (docs/DECISIONS.md sections 6 and 7).

The sequence is fixed: validate the upload, read it within the size limit,
checksum it, return early on a known checksum, extract and normalize text,
chunk it with a token window, embed every chunk, and only then write the
document and all of its chunks in one transaction. Embedding is a network
call, so it never runs while a database transaction is open.

External services arrive through the ``Embedder`` and ``Tokenizer`` protocols
and the pool, so this module runs in tests without an HTTP server or network.
Failures surface as ``AppError`` subclasses with fixed public messages.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from app.config import IngestionConfig
from app.db import (
    EMBEDDING_DIMENSIONS,
    DuplicateChecksumError,
    NewChunk,
    NewDocument,
    Pool,
    StoredDocument,
    find_document_by_sha256,
    insert_chunks,
    insert_document,
    pooled_connection,
    pooled_transaction,
)
from app.errors import (
    AppError,
    DatabaseUnavailableError,
    EmbeddingProviderError,
    EmptyDocumentError,
    UnparseableDocumentError,
    UnsupportedFileTypeError,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
)
from app.logging import log_event
from app.openai_provider import Embedder
from app.tokenizer import Tokenizer

logger = logging.getLogger(__name__)

READ_CHUNK_BYTES = 64 * 1024

# A PDF's header must appear within its first 1024 bytes (ISO 32000).
PDF_SIGNATURE = b"%PDF-"
PDF_SIGNATURE_WINDOW = 1024

# The PDF is untrusted input. Beyond its own ``PyPdfError`` family, pypdf lets
# built-in errors escape on malformed structure; every one of them means the
# file cannot be parsed, which is a 400, not a 500.
_PDF_PARSE_ERRORS = (
    PyPdfError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    ArithmeticError,
    AssertionError,
    RecursionError,
    NotImplementedError,
)

# Enough of the checksum to correlate log lines, not the idempotency key itself.
SHA256_LOG_PREFIX = 12


class Parser(Enum):
    PDF = "pdf"
    TEXT = "text"


@dataclass(frozen=True, slots=True)
class FileKind:
    """How an accepted extension is parsed and which declared types it allows."""

    parser: Parser
    content_type: str
    allowed_media_types: frozenset[str]


_PDF = FileKind(
    parser=Parser.PDF,
    content_type="application/pdf",
    allowed_media_types=frozenset({"application/pdf", "application/octet-stream"}),
)
_TEXT = FileKind(
    parser=Parser.TEXT,
    content_type="text/plain",
    allowed_media_types=frozenset({"text/plain", "application/octet-stream"}),
)
_MARKDOWN = FileKind(
    parser=Parser.TEXT,
    content_type="text/markdown",
    allowed_media_types=frozenset(
        {"text/markdown", "text/x-markdown", "text/plain", "application/octet-stream"}
    ),
)

# The extension chooses the parser; the declared media type must also be in
# that extension's allow-list. Neither alone is trusted.
SUPPORTED_EXTENSIONS: dict[str, FileKind] = {
    "pdf": _PDF,
    "txt": _TEXT,
    "md": _MARKDOWN,
    "markdown": _MARKDOWN,
}


class Upload(Protocol):
    """The parts of an uploaded file ingestion needs; ``UploadFile`` satisfies it."""

    @property
    def filename(self) -> str | None: ...

    @property
    def content_type(self) -> str | None: ...

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PageText:
    """Normalized text of one PDF page, or of a whole text document."""

    page_number: int | None
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    pages: list[PageText]
    page_count: int | None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """A chunk before it has an embedding or an identity."""

    chunk_index: int
    page_number: int | None
    content: str
    token_count: int


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_id: UUID
    filename: str
    sha256: str
    page_count: int | None
    chunk_count: int
    status: Literal["ingested", "already_ingested"]


def normalize_filename(raw: str | None) -> str:
    """Keep only the final path component, so no client path is stored.

    NUL is stripped because PostgreSQL ``text`` cannot store it; otherwise a
    NUL in the filename would reach ``insert_document`` only after a paid
    embedding call and surface as a misleading 503.
    """
    return (raw or "").replace("\x00", "").replace("\\", "/").rsplit("/", 1)[-1].strip()


def resolve_file_kind(filename: str, declared_media_type: str | None) -> FileKind:
    """Choose the parser from the extension and check the declared type fits it.

    Raises:
        UnsupportedFileTypeError: the extension is missing or not supported.
        UnsupportedMediaTypeError: the media type is absent or not allowed for
            that extension.
    """
    _, dot, extension = filename.rpartition(".")
    kind = SUPPORTED_EXTENSIONS.get(extension.lower()) if dot else None
    if kind is None:
        raise UnsupportedFileTypeError
    media_type = (declared_media_type or "").split(";", 1)[0].strip().lower()
    if media_type not in kind.allowed_media_types:
        raise UnsupportedMediaTypeError
    return kind


async def read_bounded(upload: Upload, limit: int) -> bytes:
    """Read at most ``limit`` bytes, failing as soon as one more byte arrives."""
    parts: list[bytes] = []
    total = 0
    while True:
        part = await upload.read(min(READ_CHUNK_BYTES, limit + 1 - total))
        if not part:
            return b"".join(parts)
        total += len(part)
        if total > limit:
            raise UploadTooLargeError
        parts.append(part)


_HORIZONTAL_WHITESPACE = re.compile(r"[^\S\n]+")
_SPACE_BEFORE_NEWLINE = re.compile(r" \n")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_whitespace(text: str) -> str:
    """Structural normalization only (docs/DECISIONS.md section 7.4).

    Normalizes line endings, collapses runs of horizontal whitespace to one
    space, drops trailing spaces, caps blank lines at one, and trims the ends.
    NUL characters are removed because PostgreSQL ``text`` cannot store them.
    Wording, case, and order are never changed.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _HORIZONTAL_WHITESPACE.sub(" ", text)
    text = _SPACE_BEFORE_NEWLINE.sub("\n", text)
    text = _EXCESS_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def has_pdf_signature(data: bytes) -> bool:
    return PDF_SIGNATURE in data[:PDF_SIGNATURE_WINDOW]


def extract_document(kind: FileKind, data: bytes) -> ExtractedDocument:
    """Extract normalized text, dropping pages that have none.

    Raises:
        UnparseableDocumentError: invalid UTF-8, or a PDF that is malformed or
            encrypted.
    """
    if kind.parser is Parser.PDF:
        return _extract_pdf(data)
    return _extract_text(data)


def _extract_text(data: bytes) -> ExtractedDocument:
    try:
        # Strict decoding; ``-sig`` only strips a leading byte-order mark.
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UnparseableDocumentError from None
    page = PageText(page_number=None, text=normalize_whitespace(text))
    return ExtractedDocument(pages=[page] if page.text else [], page_count=None)


def _extract_pdf(data: bytes) -> ExtractedDocument:
    if not has_pdf_signature(data):
        raise UnparseableDocumentError
    try:
        reader = PdfReader(io.BytesIO(data))
        encrypted = reader.is_encrypted
        raw_pages = [] if encrypted else [page.extract_text() for page in reader.pages]
    except _PDF_PARSE_ERRORS:
        raise UnparseableDocumentError from None
    if encrypted:
        raise UnparseableDocumentError
    pages = [
        PageText(page_number=number, text=normalize_whitespace(raw))
        for number, raw in enumerate(raw_pages, start=1)
    ]
    return ExtractedDocument(
        pages=[page for page in pages if page.text], page_count=len(raw_pages)
    )


def build_chunks(
    pages: Sequence[PageText],
    tokenizer: Tokenizer,
    *,
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[ChunkDraft]:
    """Split pages into overlapping token windows (docs/DECISIONS.md section 7.5).

    Each page is windowed on its own, so no chunk spans a page boundary and a
    chunk's single page number is always truthful. ``chunk_index`` increases
    across the whole document. The last window of a page ends exactly at the
    page's end; a trailing window wholly inside the previous one is not emitted.
    """
    step = chunk_tokens - overlap_tokens
    chunks: list[ChunkDraft] = []
    for page in pages:
        tokens = tokenizer.encode(page.text)
        start = 0
        while start < len(tokens):
            window = tokens[start : start + chunk_tokens]
            content = tokenizer.decode(window)
            if content.strip():
                chunks.append(
                    ChunkDraft(
                        chunk_index=len(chunks),
                        page_number=page.page_number,
                        content=content,
                        token_count=len(window),
                    )
                )
            if start + chunk_tokens >= len(tokens):
                break
            start += step
    return chunks


class Ingestor:
    """Coordinates one ingestion from upload to committed rows."""

    def __init__(
        self,
        *,
        pool: Pool,
        embedder: Embedder,
        tokenizer: Tokenizer,
        config: IngestionConfig,
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._tokenizer = tokenizer
        self._config = config

    @property
    def max_upload_bytes(self) -> int:
        return self._config.max_upload_bytes

    async def ingest(self, upload: Upload, *, request_id: str) -> IngestionResult:
        """Ingest ``upload``, or return the existing document for a known checksum.

        Raises:
            AppError: a controlled failure; no rows were written.
        """
        started = time.monotonic()
        filename = normalize_filename(upload.filename)
        log_event(logger, "ingestion.started", request_id=request_id, filename=filename)
        try:
            return await self._ingest(upload, filename, request_id, started)
        except AppError as exc:
            log_event(
                logger,
                "ingestion.failed",
                level=logging.WARNING if exc.status_code < 500 else logging.ERROR,
                request_id=request_id,
                error_code=exc.code,
                duration_ms=_elapsed_ms(started),
            )
            raise
        except Exception:
            log_event(
                logger,
                "ingestion.failed",
                level=logging.ERROR,
                request_id=request_id,
                error_code="internal_error",
                duration_ms=_elapsed_ms(started),
            )
            raise

    async def _ingest(
        self, upload: Upload, filename: str, request_id: str, started: float
    ) -> IngestionResult:
        kind = resolve_file_kind(filename, upload.content_type)
        data = await read_bounded(upload, self._config.max_upload_bytes)
        if not data:
            raise EmptyDocumentError
        sha256 = hashlib.sha256(data).hexdigest()

        existing = await self._find_existing(sha256)
        if existing is not None:
            return self._duplicate(existing, request_id, race=False)

        extracted = extract_document(kind, data)
        if extracted.pages:
            # Bounded off the event loop: tiktoken's own loader has no HTTP
            # timeout, and build_chunks below calls it synchronously
            # (docs/DECISIONS.md section 7.5).
            await self._tokenizer.ensure_ready()
        drafts = build_chunks(
            extracted.pages,
            self._tokenizer,
            chunk_tokens=self._config.chunk_tokens,
            overlap_tokens=self._config.chunk_overlap_tokens,
        )
        if not drafts:
            raise EmptyDocumentError
        log_event(
            logger,
            "ingestion.parsed",
            request_id=request_id,
            content_type=kind.content_type,
            sha256_prefix=sha256[:SHA256_LOG_PREFIX],
            page_count=extracted.page_count,
            chunk_count=len(drafts),
        )

        embedded_at = time.monotonic()
        vectors = await self._embedder.embed([draft.content for draft in drafts])
        if len(vectors) != len(drafts) or any(
            len(vector) != EMBEDDING_DIMENSIONS for vector in vectors
        ):
            # A provider or configuration error; nothing has been written yet.
            raise EmbeddingProviderError
        log_event(
            logger,
            "ingestion.embedded",
            request_id=request_id,
            chunk_count=len(vectors),
            duration_ms=_elapsed_ms(embedded_at),
        )

        document = NewDocument(
            id=uuid4(),
            filename=filename,
            content_type=kind.content_type,
            sha256=sha256,
            page_count=extracted.page_count,
            chunk_count=len(drafts),
        )
        chunks = [
            NewChunk(
                id=uuid4(),
                document_id=document.id,
                chunk_index=draft.chunk_index,
                page_number=draft.page_number,
                content=draft.content,
                token_count=draft.token_count,
                embedding=vector,
            )
            for draft, vector in zip(drafts, vectors, strict=True)
        ]
        winner = await self._persist(document, chunks)
        if winner is not None:
            return self._duplicate(winner, request_id, race=True)

        log_event(
            logger,
            "ingestion.persisted",
            request_id=request_id,
            document_id=str(document.id),
            content_type=document.content_type,
            sha256_prefix=sha256[:SHA256_LOG_PREFIX],
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            duration_ms=_elapsed_ms(started),
        )
        return IngestionResult(
            document_id=document.id,
            filename=document.filename,
            sha256=sha256,
            page_count=document.page_count,
            chunk_count=document.chunk_count,
            status="ingested",
        )

    async def _persist(
        self, document: NewDocument, chunks: Sequence[NewChunk]
    ) -> StoredDocument | None:
        """Write the document and all of its chunks in one transaction.

        Returns ``None`` once this request's rows are committed, or the
        document that a concurrent upload of the same bytes committed first.
        The checksum constraint, not the earlier lookup, decides
        (docs/DECISIONS.md section 6).

        Raises:
            DatabaseUnavailableError: the write failed for any other reason, or
                the conflicting document could not be read back. No rows remain.
        """
        try:
            async with pooled_transaction(self._pool) as conn:
                await insert_document(conn, document)
                await insert_chunks(conn, chunks)
        except DuplicateChecksumError:
            winner = await self._find_existing(document.sha256)
            if winner is None:
                raise DatabaseUnavailableError from None
            return winner
        return None

    async def _find_existing(self, sha256: str) -> StoredDocument | None:
        async with pooled_connection(self._pool) as conn:
            return await find_document_by_sha256(conn, sha256)

    def _duplicate(
        self, existing: StoredDocument, request_id: str, *, race: bool
    ) -> IngestionResult:
        log_event(
            logger,
            "ingestion.duplicate",
            request_id=request_id,
            document_id=str(existing.id),
            sha256_prefix=existing.sha256[:SHA256_LOG_PREFIX],
            concurrent=race,
        )
        return IngestionResult(
            document_id=existing.id,
            filename=existing.filename,
            sha256=existing.sha256,
            page_count=existing.page_count,
            chunk_count=existing.chunk_count,
            status="already_ingested",
        )


def _elapsed_ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)
