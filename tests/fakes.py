"""Deterministic stand-ins for external services and uploads, and the shared
fixture corpus.

Nothing here touches the network, tiktoken's encoding data, or OpenAI.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.config import IngestionConfig
from app.db import Pool
from app.errors import AppError
from app.ingestion import Ingestor
from app.openai_provider import GroundedAnswer
from app.retrieval import RetrievedChunk
from tests.conftest import EMBEDDING_DIMENSIONS, embedding


class FakeTokenizer:
    """One token per Unicode code point, so windows are easy to reason about."""

    async def ensure_ready(self) -> None:
        return None

    def encode(self, text: str) -> list[int]:
        return [ord(character) for character in text]

    def decode(self, tokens: Sequence[int]) -> str:
        return "".join(chr(token) for token in tokens)


class FakeEmbedder:
    """Returns a deterministic one-hot vector per text and records every call.

    ``error`` makes every call fail. ``barrier`` holds each call until that many
    callers are waiting, which lines concurrent ingestions up past their
    duplicate lookups before either of them writes.
    """

    def __init__(
        self,
        *,
        error: AppError | None = None,
        barrier: asyncio.Barrier | None = None,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        self.error = error
        self.barrier = barrier
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.barrier is not None:
            await self.barrier.wait()
        if self.error is not None:
            raise self.error
        return [
            embedding(
                hot_index=_stable_index(text) % self.dimensions,
                dimensions=self.dimensions,
            )
            for text in texts
        ]


def _stable_index(text: str) -> int:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


# Common question words that would otherwise make unrelated texts look similar.
KEYWORD_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "s",
        "the",
        "to",
        "was",
        "were",
        "what",
        "when",
        "which",
        "who",
        "why",
        "with",
    }
)

_WORD = re.compile(r"[a-z0-9]+")


class KeywordEmbedder:
    """A bag-of-words embedder, so a question lands near the chunk it is about.

    Each lower-cased word outside ``KEYWORD_STOPWORDS`` adds one to the
    dimension its BLAKE2b digest selects, and the vector is then normalized.
    The digest, unlike ``hash()``, is not salted per process, so the same text
    gives the same vector in every process and on every machine. Text with no
    counted words gives the zero vector, which pgvector's cosine distance turns
    into NaN.
    """

    def __init__(self, *, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vector(text) for text in texts]

    def vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        for word in _WORD.findall(text.casefold()):
            if word not in KEYWORD_STOPWORDS:
                values[keyword_dimension(word, self.dimensions)] += 1.0
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0.0:
            return values
        return [value / norm for value in values]


def keyword_dimension(word: str, dimensions: int = EMBEDDING_DIMENSIONS) -> int:
    """The dimension ``KeywordEmbedder`` assigns to ``word``."""
    digest = hashlib.blake2b(word.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


class FakeUpload:
    """An in-memory upload with the ``UploadFile`` surface ingestion uses."""

    def __init__(self, filename: str | None, content_type: str | None, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self._position = 0
        self.bytes_read = 0

    async def read(self, size: int = -1) -> bytes:
        end = len(self._data) if size < 0 else self._position + size
        part = self._data[self._position : end]
        self._position += len(part)
        self.bytes_read += len(part)
        return part


def build_pdf(pages: Sequence[str | None]) -> bytes:
    """A minimal valid PDF with one Helvetica text line per page.

    ``None`` produces a page with no text. The file is written by hand, with a
    correct cross-reference table, so the fixture depends on no PDF writer.
    """
    count = len(pages)
    page_ids = [4 + 2 * index for index in range(count)]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, pages, strict=True):
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {page_id + 1} 0 R >>"
            ).encode()
        )
        stream = b""
        if text:
            escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()
    return bytes(out)


class FakeRetriever:
    """A ``QueryRetriever`` with a fixed vector and scripted chunks.

    It records every question it embeds and every retrieve call.
    ``embed_error`` or ``retrieve_error`` makes that step raise instead.
    """

    def __init__(
        self,
        chunks: Sequence[RetrievedChunk] = (),
        *,
        embed_error: Exception | None = None,
        retrieve_error: Exception | None = None,
    ) -> None:
        self.chunks = list(chunks)
        self.embed_error = embed_error
        self.retrieve_error = retrieve_error
        self.questions: list[str] = []
        self.retrieve_calls = 0

    async def embed_query(self, question: str) -> list[float]:
        self.questions.append(question)
        if self.embed_error is not None:
            raise self.embed_error
        return embedding(hot_index=0)

    async def retrieve(self, query_embedding: Sequence[float]) -> list[RetrievedChunk]:
        self.retrieve_calls += 1
        if self.retrieve_error is not None:
            raise self.retrieve_error
        return list(self.chunks)


class ScriptedAnswerGenerator:
    """An ``AnswerGenerator`` that returns or raises queued outcomes in order.

    Every call is recorded as ``(instructions, prompt)``. A call with nothing
    left in the queue fails the test.
    """

    def __init__(self, *outcomes: GroundedAnswer | Exception) -> None:
        self._outcomes = deque(outcomes)
        self.calls: list[tuple[str, str]] = []

    async def generate_answer(
        self, *, instructions: str, prompt: str
    ) -> GroundedAnswer:
        self.calls.append((instructions, prompt))
        if not self._outcomes:
            raise AssertionError("unexpected answer-model call")
        outcome = self._outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# ---------------------------------------------------------------------------
# Shared fixture corpus, ingested through the real ingestion path
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
