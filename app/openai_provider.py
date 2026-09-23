"""OpenAI adapter behind an application-facing embedding interface.

Ingestion depends on ``Embedder``, never on the SDK, so tests use a
deterministic fake and make no network call (docs/DECISIONS.md section 3.3).
The adapter returns plain float lists and never leaks raw SDK responses or
errors: a provider failure surfaces as ``EmbeddingProviderError``, whose public
message is fixed, and only the failure's type and status code are logged.

Structured generation joins this module in Milestone 4.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import NoReturn, Protocol

import openai
from openai import AsyncOpenAI

from app.config import OpenAIConfig
from app.errors import EmbeddingProviderError
from app.logging import log_event

logger = logging.getLogger(__name__)

# Inputs per embeddings request. At ~800 tokens per chunk this stays well
# inside the API's per-request input and token limits.
EMBEDDING_BATCH_SIZE = 128


class Embedder(Protocol):
    """Turns texts into vectors, one per input, in input order."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def create_openai_client(config: OpenAIConfig) -> AsyncOpenAI:
    """Build the shared client. Constructing it makes no network request."""
    return AsyncOpenAI(
        api_key=config.api_key,
        timeout=config.timeout_seconds,
        max_retries=config.max_retries,
    )


class OpenAIEmbedder:
    """``Embedder`` backed by the OpenAI embeddings API."""

    def __init__(
        self,
        client: AsyncOpenAI,
        *,
        model: str,
        dimensions: int,
        batch_size: int = EMBEDDING_BATCH_SIZE,
    ) -> None:
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            vectors.extend(
                await self._embed_batch(texts[start : start + self._batch_size])
            )
        return vectors

    async def _embed_batch(self, batch: Sequence[str]) -> list[list[float]]:
        try:
            response = await self._client.embeddings.create(
                input=list(batch),
                model=self._model,
                dimensions=self._dimensions,
                encoding_format="float",
            )
        except openai.OpenAIError as exc:
            # The SDK error may quote the response body; only its type and
            # status code are safe to record.
            log_event(
                logger,
                "embedding.request_failed",
                level=logging.ERROR,
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
            )
            raise EmbeddingProviderError from None

        items = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in items] != list(range(len(batch))):
            self._reject("index_mismatch")
        vectors = [list(item.embedding) for item in items]
        if any(len(vector) != self._dimensions for vector in vectors):
            self._reject("dimension_mismatch")
        return vectors

    def _reject(self, reason: str) -> NoReturn:
        log_event(
            logger, "embedding.invalid_response", level=logging.ERROR, reason=reason
        )
        raise EmbeddingProviderError
