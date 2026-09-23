"""Retrieval unit tests: similarity filtering, query embedding, and failure
events, with no PostgreSQL and no network.

The service against real pgvector is covered in ``tests/test_retrieval_db.py``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import psycopg
import pytest

from app.config import RetrievalConfig
from app.db import ChunkMatch, Pool
from app.errors import DatabaseUnavailableError, EmbeddingProviderError
from app.logging import bind_request_id
from app.retrieval import Retriever, filter_matches
from tests.fakes import FakeEmbedder, KeywordEmbedder, keyword_dimension


def match(cosine_distance: float, content: str = "chunk") -> ChunkMatch:
    return ChunkMatch(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="acme.pdf",
        page_number=3,
        content=content,
        cosine_distance=cosine_distance,
    )


# ---------------------------------------------------------------------------
# filter_matches
# ---------------------------------------------------------------------------


def test_similarity_is_one_minus_distance_and_metadata_is_copied() -> None:
    original = match(0.25)

    (kept,) = filter_matches([original], 0.30)

    assert kept.similarity == 0.75
    assert kept.cosine_distance == 0.25
    assert (kept.chunk_id, kept.document_id) == (
        original.chunk_id,
        original.document_id,
    )
    assert (kept.filename, kept.page_number, kept.content) == ("acme.pdf", 3, "chunk")


def test_threshold_is_inclusive() -> None:
    """0.75 and 0.25 are exact in binary, so this is the boundary itself."""
    assert len(filter_matches([match(0.75)], 0.25)) == 1


def test_similarity_just_below_the_threshold_is_discarded() -> None:
    assert filter_matches([match(0.75)], math.nextafter(0.25, 1.0)) == []


def test_negative_similarity_is_discarded() -> None:
    assert filter_matches([match(1.5)], 0.0) == []


@pytest.mark.parametrize(
    "cosine_distance",
    [math.nan, -math.inf, math.inf],
    ids=["nan", "positive-infinite-similarity", "negative-infinite-similarity"],
)
def test_non_finite_similarity_is_always_discarded(cosine_distance: float) -> None:
    """A distance of -inf gives a similarity of +inf, which a bare ``>=``
    would accept at any threshold."""
    assert filter_matches([match(cosine_distance)], 0.0) == []


def test_database_order_is_preserved_and_weak_matches_removed() -> None:
    matches = [
        match(0.1, "first"),
        match(0.5, "second"),
        match(0.8, "weak"),
        match(0.6, "third"),
    ]

    kept = filter_matches(matches, 0.30)

    assert [chunk.content for chunk in kept] == ["first", "second", "third"]


def test_no_candidates_give_no_chunks() -> None:
    assert filter_matches([], 0.30) == []


# ---------------------------------------------------------------------------
# Query embedding
# ---------------------------------------------------------------------------


class _WrongCountEmbedder:
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] * 1536, [0.0] * 1536]


def make_retriever(embedder: Any = None, pool: Any = None) -> Retriever:
    return Retriever(
        pool=cast(Pool, pool),
        embedder=embedder or FakeEmbedder(),
        config=RetrievalConfig(),
    )


@pytest.mark.anyio
async def test_embed_query_sends_exactly_one_input() -> None:
    embedder = FakeEmbedder()

    vector = await make_retriever(embedder).embed_query("Why did revenue fall?")

    assert embedder.calls == [["Why did revenue fall?"]]
    assert len(vector) == 1536


@pytest.mark.anyio
async def test_embed_query_rejects_a_wrong_vector_count() -> None:
    with pytest.raises(EmbeddingProviderError):
        await make_retriever(_WrongCountEmbedder()).embed_query("question")


@pytest.mark.anyio
async def test_embed_query_rejects_a_wrong_dimension() -> None:
    with pytest.raises(EmbeddingProviderError):
        await make_retriever(FakeEmbedder(dimensions=3)).embed_query("question")


@pytest.mark.anyio
async def test_embed_query_passes_provider_errors_through() -> None:
    error = EmbeddingProviderError()

    with pytest.raises(EmbeddingProviderError) as raised:
        await make_retriever(FakeEmbedder(error=error)).embed_query("question")

    assert raised.value is error


@pytest.mark.anyio
@pytest.mark.parametrize("question", ["", "   \n"])
async def test_blank_question_is_a_caller_error_and_is_not_embedded(
    question: str,
) -> None:
    embedder = FakeEmbedder()

    with pytest.raises(ValueError, match="blank"):
        await make_retriever(embedder).embed_query(question)

    assert embedder.calls == []


# ---------------------------------------------------------------------------
# Database failure
# ---------------------------------------------------------------------------


class _BrokenConnection:
    async def __aenter__(self) -> None:
        raise psycopg.OperationalError("connection to postgresql://u:hunter2@db failed")

    async def __aexit__(self, *args: object) -> None:
        return None


class _UnreachablePool:
    def connection(self) -> _BrokenConnection:
        return _BrokenConnection()


@pytest.mark.anyio
async def test_unreachable_database_is_reported_and_logged_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    retriever = make_retriever(pool=_UnreachablePool())

    with bind_request_id("req-db-down"), pytest.raises(DatabaseUnavailableError):
        await retriever.retrieve([1.0] + [0.0] * 1535)

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.retrieval"
    ]
    assert [event["event"] for event in events] == [
        "retrieval.started",
        "retrieval.failed",
    ]
    assert events[1]["error_code"] == "database_unavailable"
    assert all(event["request_id"] == "req-db-down" for event in events)
    assert "hunter2" not in "\n".join(record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# KeywordEmbedder determinism
# ---------------------------------------------------------------------------


def test_keyword_embedder_is_deterministic_across_instances() -> None:
    text = "Why did Acme's European revenue decline?"

    assert KeywordEmbedder().vector(text) == KeywordEmbedder().vector(text)


def test_keyword_dimensions_are_pinned() -> None:
    """Fixed values: a salted ``hash()`` could not reproduce these anywhere else."""
    assert [keyword_dimension(w) for w in ("revenue", "european", "acme")] == [
        22,
        847,
        129,
    ]


def test_keyword_embedder_is_deterministic_across_processes() -> None:
    """Different ``PYTHONHASHSEED`` values change ``hash()``, never these vectors."""
    script = (
        "from tests.fakes import KeywordEmbedder; "
        "v = KeywordEmbedder().vector('European revenue declined in Germany'); "
        "print([i for i, x in enumerate(v) if x])"
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            text=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=Path(__file__).resolve().parent.parent,
        ).stdout
        for seed in ("1", "2", "3")
    }

    assert len(outputs) == 1
    expected = KeywordEmbedder().vector("European revenue declined in Germany")
    assert outputs.pop().strip() == str([i for i, x in enumerate(expected) if x])


def test_keyword_embedder_ignores_stopwords_and_normalizes() -> None:
    vector = KeywordEmbedder().vector("What is the revenue?")

    assert sum(value * value for value in vector) == pytest.approx(1.0)
    assert [i for i, value in enumerate(vector) if value] == [
        keyword_dimension("revenue")
    ]


def test_text_of_only_stopwords_embeds_to_the_zero_vector() -> None:
    assert not any(KeywordEmbedder().vector("What was it?"))
