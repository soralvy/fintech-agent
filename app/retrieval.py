"""Question embedding and exact vector retrieval (docs/DECISIONS.md section 8).

The two steps are separate methods because the answering graph runs them as
separate nodes, ``embed_query`` and ``retrieve`` (docs/DECISIONS.md sections
10.2 and 10.3). ``db.py`` returns raw cosine distances; this module converts
them to similarities and discards weak results. Surviving chunks keep the
database's order and carry only database-sourced metadata, so later citation
labels and excerpts come from trusted values, never from the model.

The minimum similarity is a configurable heuristic, not a calibrated
probability. An empty result is a normal outcome that routes to the
insufficient-context path, not an error.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from app.config import RetrievalConfig
from app.db import (
    EMBEDDING_DIMENSIONS,
    ChunkMatch,
    Pool,
    pooled_connection,
    search_chunks_by_embedding,
)
from app.errors import AppError, EmbeddingProviderError
from app.logging import log_event
from app.openai_provider import Embedder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk that passed the similarity filter, with trusted metadata."""

    chunk_id: UUID
    document_id: UUID
    filename: str
    page_number: int | None
    content: str
    cosine_distance: float
    similarity: float


def filter_matches(
    matches: Sequence[ChunkMatch], min_similarity: float
) -> list[RetrievedChunk]:
    """Keep the matches whose similarity is finite and at least ``min_similarity``.

    ``similarity = 1 - cosine_distance``. The threshold is inclusive, since
    docs/SPEC.md section 9.1 discards only candidates *below* it. A non-finite
    similarity is always rejected: pgvector returns NaN for a zero-norm
    vector, and an infinity would otherwise pass any threshold. Order is kept
    exactly as given.
    """
    accepted: list[RetrievedChunk] = []
    for match in matches:
        similarity = 1.0 - match.cosine_distance
        if math.isfinite(similarity) and similarity >= min_similarity:
            accepted.append(
                RetrievedChunk(
                    chunk_id=match.chunk_id,
                    document_id=match.document_id,
                    filename=match.filename,
                    page_number=match.page_number,
                    content=match.content,
                    cosine_distance=match.cosine_distance,
                    similarity=similarity,
                )
            )
    return accepted


class Retriever:
    """Embeds questions and returns the nearest chunks that pass the filter."""

    def __init__(
        self, *, pool: Pool, embedder: Embedder, config: RetrievalConfig
    ) -> None:
        self._pool = pool
        self._embedder = embedder
        self._config = config

    async def embed_query(self, question: str) -> list[float]:
        """Embed one validated, non-blank question with the ingestion embedding model.

        Raises:
            ValueError: ``question`` is blank. Callers validate it first.
            EmbeddingProviderError: the provider failed, or did not return
                exactly one vector of the stored dimension.
        """
        if not question.strip():
            raise ValueError("question must not be blank")
        vectors = await self._embedder.embed([question])
        if len(vectors) != 1 or len(vectors[0]) != EMBEDDING_DIMENSIONS:
            raise EmbeddingProviderError
        return vectors[0]

    async def retrieve(self, query_embedding: Sequence[float]) -> list[RetrievedChunk]:
        """Return up to ``top_k`` chunks by exact cosine search, weak ones removed.

        Raises:
            DatabaseUnavailableError: the search could not run.
        """
        top_k = self._config.top_k
        min_similarity = self._config.min_similarity
        started = time.monotonic()
        log_event(
            logger,
            "retrieval.started",
            top_k=top_k,
            minimum_similarity=min_similarity,
        )
        try:
            async with pooled_connection(self._pool) as conn:
                matches = await search_chunks_by_embedding(conn, query_embedding, top_k)
        except AppError as exc:
            self._failed(exc.code, started)
            raise
        except Exception:
            self._failed("internal_error", started)
            raise

        accepted = filter_matches(matches, min_similarity)
        top_similarity = 1.0 - matches[0].cosine_distance if matches else None
        log_event(
            logger,
            "retrieval.completed",
            top_k=top_k,
            candidate_count=len(matches),
            accepted_count=len(accepted),
            minimum_similarity=min_similarity,
            top_similarity=(
                top_similarity
                if top_similarity is not None and math.isfinite(top_similarity)
                else None
            ),
            duration_ms=_elapsed_ms(started),
        )
        return accepted

    def _failed(self, error_code: str, started: float) -> None:
        log_event(
            logger,
            "retrieval.failed",
            level=logging.ERROR,
            error_code=error_code,
            duration_ms=_elapsed_ms(started),
        )


def _elapsed_ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)
