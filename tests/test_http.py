"""HTTP contract tests for ``POST /v1/documents``, ``POST /v1/query``, the
error envelopes, and application startup.

Validation and error-mapping tests run without a database: they either fail
before the database is reached, use a pool that refuses to connect, or
override the query graph with one built over deterministic fakes. Tests that
store documents run the real lifespan (``TestClient`` as a context manager)
against the test database, with the embedder and tokenizer replaced by
deterministic fakes. No test calls OpenAI or loads tiktoken encoding data.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import socket
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, TextIO, cast, get_args
from uuid import UUID, uuid4

import httpx
import httpx2
import langchain_core.tracers.langchain
import langsmith.utils
import psycopg
import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message, Receive, Scope
from starlette.types import Send as ASGISend

from app.citations import INSUFFICIENT_CONTEXT_ANSWER, DocumentCitation, McpCitation
from app.config import (
    TRACING_ENV_VARS,
    ConfigError,
    IngestionConfig,
    MarketDataConfig,
    RetrievalConfig,
)
from app.db import Pool
from app.errors import AnswerProviderError, EmbeddingProviderError
from app.graph import QueryGraph, QueryResult, QueryRetriever, build_query_graph
from app.ingestion import Ingestor
from app.main import (
    UnexpectedErrorMiddleware,
    app,
    get_ingestor,
    get_query_graph,
    http_error_handler,
    open_market_tools,
    optional_market_tools,
    request_validation_error_handler,
    to_query_response,
)
from app.market_data import MarketDataError, MarketDataProvider, MarketQuote
from app.mcp_client import (
    ALLOWED_TOOLS,
    MarketDataTools,
    open_market_data_tools,
    stdio_server_parameters,
)
from app.mcp_server import build_mcp_server
from app.openai_provider import Embedder, GroundedAnswer, OpenAIToolPlanner, ToolPlan
from app.prompts import render_tool_plan_input
from app.retrieval import RetrievedChunk, Retriever
from app.schemas import McpQueryCitation, QueryResponse
from app.tokenizer import TiktokenTokenizer, Tokenizer
from tests.db_safety import reset_test_database
from tests.fakes import (
    BarrierMarketDataProvider,
    FakeEmbedder,
    FakeRetriever,
    FakeTokenizer,
    KeywordEmbedder,
    ScriptedAnswerGenerator,
    ScriptedMarketDataProvider,
    ScriptedToolPlanner,
    build_pdf,
    quote_for,
)

URL = "/v1/documents"
DUMMY_KEY = "test-key-not-a-secret"
UPLOAD_LIMIT = 4096


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


UNSUPPORTED_FILE_TYPE = error_body(
    "unsupported_file_type", "Supported file types are PDF, Markdown, and plain text."
)
UNSUPPORTED_MEDIA_TYPE = error_body(
    "unsupported_media_type",
    "The declared media type is missing or does not match the file extension.",
)
INVALID_REQUEST = error_body(
    "invalid_request",
    "The request must be multipart/form-data with exactly one 'file' upload.",
)


def raw_multipart(
    *parts: tuple[str, str | None, str | None, bytes],
) -> tuple[bytes, dict[str, str]]:
    """Build a multipart body by hand, to control headers httpx would add.

    Each part is ``(field, filename, content_type, data)``; ``None`` omits the
    filename or the Content-Type header.
    """
    boundary = "test-boundary-7d1c"
    body = bytearray()
    for field, filename, content_type, data in parts:
        disposition = f'form-data; name="{field}"'
        if filename is not None:
            disposition += f'; filename="{filename}"'
        body += f"--{boundary}\r\nContent-Disposition: {disposition}\r\n".encode()
        if content_type is not None:
            body += f"Content-Type: {content_type}\r\n".encode()
        body += b"\r\n" + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


# ---------------------------------------------------------------------------
# Without a database
# ---------------------------------------------------------------------------


class _RefusingPool:
    """A pool whose every connection attempt fails like an outage."""

    def __init__(self, message: str) -> None:
        self.message = message
        self.attempts = 0

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[None]:
        self.attempts += 1
        raise psycopg.OperationalError(self.message)
        yield  # pragma: no cover


@dataclass
class OfflineApp:
    client: TestClient
    pool: _RefusingPool
    embedder: FakeEmbedder


@pytest.fixture
def offline() -> Iterator[OfflineApp]:
    pool = _RefusingPool("could not connect to postgresql://app:hunter2@db.internal")
    embedder = FakeEmbedder()
    ingestor = Ingestor(
        pool=cast(Pool, pool),
        embedder=embedder,
        tokenizer=FakeTokenizer(),
        config=IngestionConfig(max_upload_bytes=UPLOAD_LIMIT),
    )
    app.dependency_overrides[get_ingestor] = lambda: ingestor
    try:
        yield OfflineApp(TestClient(app), pool, embedder)
    finally:
        app.dependency_overrides.clear()


def test_unsupported_extension_is_415(offline: OfflineApp) -> None:
    response = offline.client.post(
        URL, files={"file": ("run.exe", b"MZ", "application/octet-stream")}
    )

    assert response.status_code == 415
    assert response.json() == UNSUPPORTED_FILE_TYPE


def test_extension_and_media_type_mismatch_is_415(offline: OfflineApp) -> None:
    response = offline.client.post(
        URL, files={"file": ("report.pdf", build_pdf(["x"]), "text/plain")}
    )

    assert response.status_code == 415
    assert response.json() == UNSUPPORTED_MEDIA_TYPE


def test_missing_media_type_is_415(offline: OfflineApp) -> None:
    body, headers = raw_multipart(("file", "notes.txt", None, b"text"))

    response = offline.client.post(URL, content=body, headers=headers)

    assert response.status_code == 415
    assert response.json() == UNSUPPORTED_MEDIA_TYPE


def test_octet_stream_with_unsupported_extension_is_415(offline: OfflineApp) -> None:
    response = offline.client.post(
        URL, files={"file": ("data.csv", b"a,b", "application/octet-stream")}
    )

    assert response.status_code == 415
    assert response.json() == UNSUPPORTED_FILE_TYPE


def test_upload_over_the_limit_is_413(offline: OfflineApp) -> None:
    response = offline.client.post(
        URL, files={"file": ("big.txt", b"x" * (UPLOAD_LIMIT + 1), "text/plain")}
    )

    assert response.status_code == 413
    assert response.json() == error_body(
        "file_too_large", "The uploaded file exceeds the maximum allowed size."
    )
    assert offline.pool.attempts == 0


def test_oversized_content_length_is_rejected_before_parsing(
    offline: OfflineApp,
) -> None:
    response = offline.client.post(
        URL,
        content=b"x" * (UPLOAD_LIMIT + 70_000),
        headers={"Content-Type": "multipart/form-data; boundary=unused"},
    )

    assert response.status_code == 413


def test_empty_file_is_400(offline: OfflineApp) -> None:
    response = offline.client.post(
        URL, files={"file": ("empty.txt", b"", "text/plain")}
    )

    assert response.status_code == 400
    assert response.json() == error_body(
        "empty_document", "The document contains no extractable text."
    )


Send = Callable[[TestClient], httpx.Response]


@pytest.mark.parametrize(
    "send",
    [
        pytest.param(lambda c: c.post(URL), id="no-body"),
        pytest.param(lambda c: c.post(URL, json={"file": "x"}), id="json"),
        pytest.param(
            lambda c: c.post(URL, data={"file": "not a file"}), id="form-field"
        ),
        pytest.param(
            lambda c: c.post(URL, files={"other": ("a.txt", b"x", "text/plain")}),
            id="wrong-field",
        ),
        pytest.param(
            lambda c: c.post(
                URL,
                files=[
                    ("file", ("a.txt", b"x", "text/plain")),
                    ("file", ("b.txt", b"y", "text/plain")),
                ],
            ),
            id="two-files",
        ),
        pytest.param(
            lambda c: c.post(
                URL,
                files={"file": ("a.txt", b"x", "text/plain")},
                data={"extra": "field"},
            ),
            id="extra-field",
        ),
        pytest.param(
            lambda c: c.post(
                URL,
                content=b"--broken\r\nnot multipart",
                headers={"Content-Type": "multipart/form-data; boundary=x"},
            ),
            id="malformed",
        ),
    ],
)
def test_malformed_requests_are_422_in_the_envelope(
    offline: OfflineApp, send: Send
) -> None:
    response = send(offline.client)

    assert response.status_code == 422
    assert response.json() == INVALID_REQUEST


def test_database_outage_is_503_without_driver_details(offline: OfflineApp) -> None:
    response = offline.client.post(
        URL, files={"file": ("a.txt", b"Revenue grew.", "text/plain")}
    )

    assert response.status_code == 503
    assert response.json() == error_body(
        "database_unavailable", "The database is unavailable."
    )
    assert "hunter2" not in response.text and "db.internal" not in response.text
    assert offline.embedder.calls == []


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_startup_fails_without_an_openai_key(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    created: list[object] = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:dbpass@127.0.0.1:1/x")
    if value is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", value)
    monkeypatch.setattr("app.main.create_pool", lambda config: created.append(config))

    with pytest.raises(ConfigError) as raised, TestClient(app):
        pass  # pragma: no cover

    assert str(raised.value) == "OPENAI_API_KEY is not set"
    assert created == [], "no resource may be created before configuration passes"


def test_startup_refuses_an_unpinned_embedding_model_before_any_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:dbpass@127.0.0.1:1/x")
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setattr("app.main.create_pool", lambda config: created.append(config))
    monkeypatch.setattr(
        "app.main.create_openai_client", lambda config: created.append(config)
    )

    with pytest.raises(ConfigError) as raised, TestClient(app):
        pass  # pragma: no cover

    assert str(raised.value).startswith("OPENAI_EMBEDDING_MODEL must be")
    assert created == [], "no resource may be created before configuration passes"


# ---------------------------------------------------------------------------
# With the real lifespan and the test database
# ---------------------------------------------------------------------------


@dataclass
class LiveAppOf[E: Embedder]:
    client: TestClient
    embedder: E
    database_url: str

    def counts(self) -> tuple[int, int]:
        with psycopg.connect(self.database_url) as conn:
            documents = conn.execute("SELECT count(*) FROM documents").fetchone()
            chunks = conn.execute("SELECT count(*) FROM document_chunks").fetchone()
        assert documents is not None and chunks is not None
        return documents[0], chunks[0]


LiveApp = LiveAppOf[FakeEmbedder]


@pytest.fixture
def migrated_database_url(database_url: str) -> str:
    """A freshly migrated, empty schema, prepared synchronously.

    The lifespan pool lives on ``TestClient``'s own event loop, so the schema
    is reset on a short-lived loop of its own rather than through the async
    ``db`` fixture, using the same guarded helper.
    """
    asyncio.run(reset_test_database(database_url))
    return database_url


@pytest.fixture
def live_env(migrated_database_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("DATABASE_URL", migrated_database_url)
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("MAX_UPLOAD_BYTES", str(UPLOAD_LIMIT * 16))
    return migrated_database_url


def start_live[E: Embedder](
    database_url: str,
    embedder: E,
    tokenizer: Tokenizer,
    *,
    query_graph: Callable[[Request], QueryGraph] | None = None,
) -> Iterator[LiveAppOf[E]]:
    """Run the real lifespan with the ingestor, and optionally the query
    graph, replaced; both overrides are always cleared on exit."""

    def fake_ingestor(request: Request) -> Ingestor:
        return Ingestor(
            pool=request.app.state.pool,
            embedder=embedder,
            tokenizer=tokenizer,
            config=IngestionConfig(max_upload_bytes=UPLOAD_LIMIT * 16),
        )

    app.dependency_overrides[get_ingestor] = fake_ingestor
    if query_graph is not None:
        app.dependency_overrides[get_query_graph] = query_graph
    try:
        with TestClient(app) as client:
            yield LiveAppOf(client, embedder, database_url)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def live(live_env: str) -> Iterator[LiveApp]:
    yield from start_live(live_env, FakeEmbedder(), FakeTokenizer())


def test_text_upload_is_201_and_persisted(live: LiveApp) -> None:
    data = b"Acme reported that European revenue declined 4% on FX headwinds."

    response = live.client.post(URL, files={"file": ("acme.txt", data, "text/plain")})

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {
        "document_id",
        "filename",
        "sha256",
        "page_count",
        "chunk_count",
        "status",
    }
    assert body["filename"] == "acme.txt"
    assert body["page_count"] is None
    assert body["chunk_count"] == 1
    assert body["status"] == "ingested"
    assert live.counts() == (1, 1)


def test_markdown_and_pdf_uploads_with_octet_stream(live: LiveApp) -> None:
    markdown = live.client.post(
        URL, files={"file": ("n.markdown", b"# Risks", "application/octet-stream")}
    )
    pdf = live.client.post(
        URL,
        files={
            "file": (
                "r.pdf",
                build_pdf(["Page one", None, "Page three"]),
                "application/octet-stream",
            )
        },
    )

    assert markdown.status_code == 201
    assert pdf.status_code == 201
    assert pdf.json()["page_count"] == 3
    assert pdf.json()["chunk_count"] == 2
    assert live.counts() == (2, 3)


def test_duplicate_upload_is_200_and_adds_no_chunks(live: LiveApp) -> None:
    upload = {"file": ("acme.txt", b"Identical bytes. " * 100, "text/plain")}

    first = live.client.post(URL, files=upload)
    second = live.client.post(URL, files=upload)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == {**first.json(), "status": "already_ingested"}
    assert len(live.embedder.calls) == 1
    assert live.counts() == (1, first.json()["chunk_count"])


def test_invalid_utf8_is_400(live: LiveApp) -> None:
    response = live.client.post(
        URL, files={"file": ("bad.txt", b"ok \xff\xfe", "text/plain")}
    )

    assert response.status_code == 400
    assert response.json() == error_body(
        "unparseable_document", "The document could not be parsed as its declared type."
    )
    assert live.counts() == (0, 0)


def test_embedding_failure_is_502_and_leaves_no_rows(live_env: str) -> None:
    embedder = FakeEmbedder(error=EmbeddingProviderError())

    for live in start_live(live_env, embedder, FakeTokenizer()):
        response = live.client.post(
            URL, files={"file": ("a.txt", b"Revenue grew.", "text/plain")}
        )

        assert response.status_code == 502
        assert response.json() == error_body(
            "embedding_provider_error",
            "The embedding provider is unavailable or returned an invalid response.",
        )
        assert live.counts() == (0, 0)


def test_tokenizer_failure_is_503_without_details(
    live_env: str, caplog: pytest.LogCaptureFixture
) -> None:
    cache_path = "/Users/someone/Library/Caches/tiktoken/9b5ad71b2ce5302211f9c615"

    def failing_loader(name: str) -> NoReturn:
        raise OSError(f"HTTP 403 <html>denied</html> while writing {cache_path}")

    caplog.set_level(logging.DEBUG)
    tokenizer = TiktokenTokenizer(loader=failing_loader)

    for live in start_live(live_env, FakeEmbedder(), tokenizer):
        response = live.client.post(
            URL, files={"file": ("a.txt", b"Revenue grew.", "text/plain")}
        )

        assert response.status_code == 503
        assert response.json() == error_body(
            "tokenizer_unavailable", "The tokenizer is temporarily unavailable."
        )
        assert live.counts() == (0, 0)
        assert live.embedder.calls == []

    events = [
        json.loads(r.getMessage()) for r in caplog.records if r.name.startswith("app.")
    ]
    (started,) = [e for e in events if e["event"] == "ingestion.started"]
    # The adapter passes no request ID; the ingestion-scoped binding adds it,
    # even though the load ran in a worker thread.
    assert {
        "event": "tokenizer.load_failed",
        "encoding": "cl100k_base",
        "error_type": "OSError",
        "request_id": started["request_id"],
    } in events
    assert any(
        e["event"] == "ingestion.failed" and e["error_code"] == "tokenizer_unavailable"
        for e in events
    )
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert cache_path not in logged and "denied" not in logged


def test_real_lifespan_wires_adapters_without_touching_the_network(
    live_env: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Startup builds the real OpenAI and tiktoken adapters but calls neither.

    ``conftest`` makes any tiktoken load fail, so the first upload -- the only
    thing that loads the encoding -- is a safe 503 before OpenAI is reached.
    """
    caplog.set_level(logging.DEBUG)

    with TestClient(app) as client:
        ingestor: Ingestor = app.state.ingestor
        assert ingestor.max_upload_bytes == UPLOAD_LIMIT * 16

        response = client.post(
            URL, files={"file": ("a.txt", b"Revenue grew.", "text/plain")}
        )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "tokenizer_unavailable"
    assert DUMMY_KEY not in "\n".join(r.getMessage() for r in caplog.records)
    with psycopg.connect(live_env) as conn:
        row = conn.execute("SELECT count(*) FROM documents").fetchone()
    assert row == (0,)


# ===========================================================================
# POST /v1/query (Milestone 4)
# ===========================================================================

QUERY_URL = "/v1/query"
QUESTION = "Why did Acme's European revenue decline?"
SENTINEL = "sentinel-input-51c"
GENERIC_INVALID = error_body(
    "invalid_request", "The request is malformed or failed validation."
)
INTERNAL_ERROR = error_body("internal_error", "The request could not be completed.")
INSUFFICIENT_BODY = {
    "answer": INSUFFICIENT_CONTEXT_ANSWER,
    "status": "insufficient_context",
    "citations": [],
    "tools_used": [],
}


def evidence(
    content: str = "European revenue declined 4% on currency headwinds.",
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        filename="acme-fy2025.txt",
        page_number=None,
        content=content,
        cosine_distance=0.2,
        similarity=0.8,
    )


def grounded(
    answer: str = "Revenue declined 4% [D1].", citation_ids: list[str] | None = None
) -> GroundedAnswer:
    return GroundedAnswer(
        answer=answer,
        citation_ids=["D1"] if citation_ids is None else citation_ids,
        insufficient_context=False,
    )


@contextmanager
def serving(graph: object) -> Iterator[TestClient]:
    """A client whose query graph is overridden; no lifespan runs, so nothing
    reaches the database or OpenAI."""
    app.dependency_overrides[get_query_graph] = lambda: graph
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.clear()


def fake_graph(
    retriever: FakeRetriever, answerer: ScriptedAnswerGenerator
) -> QueryGraph:
    return build_query_graph(retriever=retriever, answerer=answerer)


def events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name.startswith("app")
    ]


def named(caplog: pytest.LogCaptureFixture, name: str) -> list[dict[str, Any]]:
    return [event for event in events(caplog) if event["event"] == name]


def assert_logs_hold_none_of(caplog: pytest.LogCaptureFixture, *texts: str) -> None:
    """Check both the raw messages and the formatted output of every record,
    from every logger, and that no record carries exception info."""
    for record in caplog.records:
        assert record.exc_info is None, record.name
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for text in (*texts, "Traceback"):
        assert text not in logged
        assert text not in caplog.text


# ---------------------------------------------------------------------------
# Success and insufficient context (offline)
# ---------------------------------------------------------------------------


def test_query_answers_with_trusted_citations() -> None:
    chunk = evidence()
    answerer = ScriptedAnswerGenerator(
        grounded("Revenue declined 4% [D1] [D9].", ["D1", "D9"])
    )
    retriever = FakeRetriever([chunk])

    with serving(fake_graph(retriever, answerer)) as client:
        response = client.post(QUERY_URL, json={"question": f"  {QUESTION}  "})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Revenue declined 4% [D1].",
        "status": "answered",
        "citations": [
            {
                "id": "D1",
                "source_type": "document",
                "document_id": str(chunk.document_id),
                "chunk_id": str(chunk.chunk_id),
                "filename": "acme-fy2025.txt",
                "page": None,
                "excerpt": chunk.content,
            }
        ],
        "tools_used": [],
    }
    assert retriever.questions == [QUESTION], "the request model trims the question"


def test_no_evidence_is_the_fixed_insufficient_body_without_a_model_call() -> None:
    answerer = ScriptedAnswerGenerator()

    with serving(fake_graph(FakeRetriever([]), answerer)) as client:
        response = client.post(QUERY_URL, json={"question": QUESTION})

    assert response.status_code == 200
    assert response.json() == INSUFFICIENT_BODY
    assert answerer.calls == []


def test_use_tools_true_without_available_tools_takes_the_document_path() -> None:
    """Tools unavailable (M6 T2): the graph has no planner or MCP client, as
    when the API starts without ``ALPHA_VANTAGE_API_KEY``, so ``use_tools``
    changes nothing and no tool is reported."""
    chunk = evidence()
    bodies = []
    for use_tools in (False, True):
        answerer = ScriptedAnswerGenerator(grounded())
        with serving(fake_graph(FakeRetriever([chunk]), answerer)) as client:
            response = client.post(
                QUERY_URL, json={"question": QUESTION, "use_tools": use_tools}
            )
        assert response.status_code == 200
        assert len(answerer.calls) == 1
        bodies.append(response.json())

    assert bodies[0] == bodies[1]
    assert bodies[1]["tools_used"] == []


# ---------------------------------------------------------------------------
# Validation (AC7)
# ---------------------------------------------------------------------------

QuerySend = Callable[[TestClient], httpx.Response]


@pytest.mark.parametrize(
    "send",
    [
        pytest.param(
            lambda c: c.post(QUERY_URL, json={"question": "  ab  "}),
            id="two-after-trim",
        ),
        pytest.param(
            lambda c: c.post(QUERY_URL, json={"question": SENTINEL * 200}), id="2001"
        ),
        pytest.param(lambda c: c.post(QUERY_URL, json={}), id="missing-question"),
        pytest.param(
            lambda c: c.post(QUERY_URL, json={"question": 12345}), id="numeric-question"
        ),
        pytest.param(
            lambda c: c.post(
                QUERY_URL,
                json={"question": f"{SENTINEL} {QUESTION}", "use_tools": "true"},
            ),
            id="string-use-tools",
        ),
        pytest.param(
            lambda c: c.post(
                QUERY_URL, json={"question": f"{SENTINEL} {QUESTION}", "use_tools": 1}
            ),
            id="integer-use-tools",
        ),
        pytest.param(
            lambda c: c.post(QUERY_URL, json={"question": QUESTION, "extra": SENTINEL}),
            id="extra-field",
        ),
        pytest.param(
            lambda c: c.post(
                QUERY_URL,
                content=f'{{"question": "{SENTINEL}"'.encode(),
                headers={"Content-Type": "application/json"},
            ),
            id="malformed-json",
        ),
        pytest.param(
            lambda c: c.post(
                QUERY_URL,
                content=f'{{"question": "{SENTINEL} {QUESTION}"}}'.encode(),
                headers={"Content-Type": "text/plain"},
            ),
            id="text-plain",
        ),
        pytest.param(
            lambda c: c.post(
                QUERY_URL,
                content=f'{{"question":"\xff {SENTINEL}"}}'.encode("latin-1"),
                headers={"Content-Type": "application/json"},
            ),
            id="non-utf8",
        ),
    ],
)
def test_malformed_queries_are_422_with_the_generic_message(send: QuerySend) -> None:
    retriever = FakeRetriever([evidence()])
    graph = fake_graph(retriever, ScriptedAnswerGenerator())

    with serving(graph) as client:
        response = send(client)

    assert response.status_code == 422
    assert response.json() == GENERIC_INVALID
    assert SENTINEL not in response.text
    assert retriever.questions == []


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": QUERY_URL,
            "headers": [],
            "query_string": b"",
        }
    )


def test_the_validation_handler_never_echoes_the_input() -> None:
    exc = RequestValidationError(
        [
            {
                "type": "string_too_short",
                "loc": ("body", "question"),
                "msg": f"too short: {SENTINEL}",
                "input": SENTINEL,
            }
        ]
    )
    assert SENTINEL in str(exc.errors()), "the check below is not vacuous"

    response = asyncio.run(request_validation_error_handler(_request(), exc))

    assert response.status_code == 422
    assert json.loads(bytes(response.body)) == GENERIC_INVALID
    assert SENTINEL.encode() not in bytes(response.body)


def test_the_http_exception_handler_maps_a_body_parse_400_to_the_envelope() -> None:
    exc = StarletteHTTPException(400, "There was an error parsing the body")

    response = asyncio.run(http_error_handler(_request(), exc))

    assert response.status_code == 422
    assert json.loads(bytes(response.body)) == GENERIC_INVALID


def test_the_http_exception_handler_keeps_other_statuses_unchanged() -> None:
    exc = StarletteHTTPException(503, "database unavailable")

    response = asyncio.run(http_error_handler(_request(), exc))

    assert response.status_code == 503
    assert json.loads(bytes(response.body)) == {"detail": "database unavailable"}


def test_an_unknown_route_keeps_the_framework_404() -> None:
    with serving(object()) as client:
        response = client.get("/no-such-route")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# ---------------------------------------------------------------------------
# Upstream failures (AC7)
# ---------------------------------------------------------------------------


def test_a_query_embedding_failure_is_502() -> None:
    retriever = FakeRetriever(embed_error=EmbeddingProviderError())

    with serving(fake_graph(retriever, ScriptedAnswerGenerator())) as client:
        response = client.post(QUERY_URL, json={"question": QUESTION})

    assert response.status_code == 502
    assert response.json() == error_body(
        "embedding_provider_error",
        "The embedding provider is unavailable or returned an invalid response.",
    )


def test_an_answer_model_failure_is_502() -> None:
    answerer = ScriptedAnswerGenerator(AnswerProviderError())

    with serving(fake_graph(FakeRetriever([evidence()]), answerer)) as client:
        response = client.post(QUERY_URL, json={"question": QUESTION})

    assert response.status_code == 502
    assert response.json() == error_body(
        "answer_provider_error",
        "The answer model is unavailable or returned an invalid response.",
    )


def test_a_retrieval_database_outage_is_503_without_driver_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    pool = _RefusingPool("could not connect to postgresql://app:hunter2@db.internal")
    retriever = Retriever(
        pool=cast(Pool, pool), embedder=FakeEmbedder(), config=RetrievalConfig()
    )
    graph = build_query_graph(retriever=retriever, answerer=ScriptedAnswerGenerator())

    with serving(graph) as client:
        response = client.post(QUERY_URL, json={"question": QUESTION})

    assert response.status_code == 503
    assert response.json() == error_body(
        "database_unavailable", "The database is unavailable."
    )
    assert pool.attempts == 1
    assert "hunter2" not in response.text and "db.internal" not in response.text
    assert_logs_hold_none_of(caplog, "hunter2", "db.internal", "postgresql://")


# ---------------------------------------------------------------------------
# Unexpected exceptions (AC7; D24)
# ---------------------------------------------------------------------------


def test_an_unexpected_node_failure_is_the_500_envelope_and_one_safe_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answerer = ScriptedAnswerGenerator(RuntimeError("secret-detail sk-test-0000"))

    with serving(fake_graph(FakeRetriever([evidence()]), answerer)) as client:
        response = client.post(QUERY_URL, json={"question": QUESTION})

    assert response.status_code == 500
    assert response.json() == INTERNAL_ERROR
    (failed,) = named(caplog, "http.request.failed")
    assert failed == {
        "event": "http.request.failed",
        "request_id": failed["request_id"],
        "status_code": 500,
        "error_code": "internal_error",
        "error_type": "unexpected_error",
    }
    (graph_failed,) = named(caplog, "graph.failed")
    assert graph_failed["request_id"] == failed["request_id"]
    assert_logs_hold_none_of(caplog, "secret-detail", "sk-test-0000", QUESTION)


class _StubGraph:
    """A graph whose final state makes response construction fail."""

    def __init__(self, state: dict[str, object]) -> None:
        self.state = state

    async def ainvoke(self, input: object, *args: object, **kwargs: object) -> object:
        return self.state


def test_a_response_validation_failure_is_500_without_its_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    answer_sentinel = "sentinel-answer-6f2"
    excerpt_sentinel = "sentinel-excerpt-8a4"
    citation = DocumentCitation(
        id="D1",
        document_id=uuid4(),
        chunk_id=uuid4(),
        filename="acme.txt",
        page=None,
        excerpt=cast(str, [excerpt_sentinel]),
    )
    state: dict[str, object] = {
        "status": "answered",
        "answer": [answer_sentinel],
        "citations": [citation],
    }
    with pytest.raises(ValidationError) as direct:
        to_query_response(
            QueryResult(
                status="answered",
                answer=cast(str, [answer_sentinel]),
                citations=(citation,),
            )
        )
    assert answer_sentinel in str(direct.value), "the log check is not vacuous"
    assert excerpt_sentinel in str(direct.value), "the log check is not vacuous"
    caplog.set_level(logging.DEBUG)

    with serving(_StubGraph(state)) as client:
        response = client.post(QUERY_URL, json={"question": QUESTION})

    assert response.status_code == 500
    assert response.json() == INTERNAL_ERROR
    (failed,) = named(caplog, "http.request.failed")
    assert failed["error_type"] == "validation_error"
    assert_logs_hold_none_of(caplog, answer_sentinel, excerpt_sentinel)


class _ExplodingIngestor:
    max_upload_bytes = UPLOAD_LIMIT

    async def ingest(self, upload: object, *, request_id: str) -> NoReturn:
        raise RuntimeError("secret-detail")


def test_an_unexpected_ingestion_failure_is_the_500_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    app.dependency_overrides[get_ingestor] = _ExplodingIngestor
    try:
        client = TestClient(app, raise_server_exceptions=True)
        response = client.post(
            URL, files={"file": ("a.txt", b"Revenue grew.", "text/plain")}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json() == INTERNAL_ERROR
    (failed,) = named(caplog, "http.request.failed")
    assert (failed["status_code"], failed["error_type"]) == (500, "unexpected_error")
    assert_logs_hold_none_of(caplog, "secret-detail")


# --- UnexpectedErrorMiddleware over stub ASGI apps --------------------------


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


def _http_scope() -> Scope:
    return {"type": "http", "method": "GET", "path": "/", "headers": []}


@pytest.mark.anyio
async def test_middleware_after_the_response_started_sends_nothing_more(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def started_then_failed(
        scope: Scope, receive: Receive, send: ASGISend
    ) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        raise RuntimeError("secret-detail")

    await UnexpectedErrorMiddleware(started_then_failed)(_http_scope(), _receive, send)

    assert [message["type"] for message in sent] == ["http.response.start"]
    assert len(named(caplog, "http.request.failed")) == 1
    assert_logs_hold_none_of(caplog, "secret-detail")


@pytest.mark.anyio
async def test_middleware_lets_cancellation_propagate_and_logs_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def cancelled(scope: Scope, receive: Receive, send: ASGISend) -> None:
        raise asyncio.CancelledError

    async def send(message: Message) -> None:
        raise AssertionError("nothing may be sent")

    with pytest.raises(asyncio.CancelledError):
        await UnexpectedErrorMiddleware(cancelled)(_http_scope(), _receive, send)

    assert events(caplog) == []


@pytest.mark.anyio
async def test_middleware_passes_a_lifespan_scope_through_untouched() -> None:
    seen: list[tuple[Scope, Receive, ASGISend]] = []

    async def inner(scope: Scope, receive: Receive, send: ASGISend) -> None:
        seen.append((scope, receive, send))
        raise ConfigError("startup must still fail")

    async def send(message: Message) -> None:
        return None

    scope: Scope = {"type": "lifespan"}

    with pytest.raises(ConfigError):
        await UnexpectedErrorMiddleware(inner)(scope, _receive, send)

    assert seen == [(scope, _receive, send)]


@pytest.mark.anyio
async def test_middleware_binds_a_fresh_request_id_per_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    async def failing(scope: Scope, receive: Receive, send: ASGISend) -> None:
        raise RuntimeError("x")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    for _ in range(2):
        await UnexpectedErrorMiddleware(failing)(_http_scope(), _receive, send)

    ids = [event["request_id"] for event in named(caplog, "http.request.failed")]
    assert len(ids) == 2 and ids[0] != ids[1]
    assert all(len(request_id) == 32 for request_id in ids)
    assert [m["type"] for m in sent] == [
        "http.response.start",
        "http.response.body",
    ] * 2
    assert sent[0]["status"] == 500


# ---------------------------------------------------------------------------
# Public MCP citation model (M6 T34; pure, no lifespan)
# ---------------------------------------------------------------------------

MCP_CITATION = McpCitation(
    id="T1",
    tool="get_market_quote",
    provider="alpha_vantage",
    symbol="ACME",
    as_of="2026-09-24",
    fields=(
        ("price", "123.45"),
        ("previous_close", "122.10"),
        ("change", "1.35"),
        ("change_percent", "1.11%"),
        ("volume", "12345678"),
        ("latest_trading_day", "2026-09-24"),
    ),
)


def test_both_citation_types_map_to_their_public_shapes() -> None:
    document = DocumentCitation(
        id="D1",
        document_id=uuid4(),
        chunk_id=uuid4(),
        filename="acme.txt",
        page=None,
        excerpt="Revenue fell.",
    )

    response = to_query_response(
        QueryResult(
            status="answered",
            answer="Fell [D1]; quote [T1].",
            citations=(document, MCP_CITATION),
        )
    )
    body = json.loads(response.model_dump_json())

    assert body["citations"] == [
        {
            "id": "D1",
            "source_type": "document",
            "document_id": str(document.document_id),
            "chunk_id": str(document.chunk_id),
            "filename": "acme.txt",
            "page": None,
            "excerpt": "Revenue fell.",
        },
        {
            "id": "T1",
            "source_type": "mcp",
            "tool": "get_market_quote",
            "provider": "alpha_vantage",
            "symbol": "ACME",
            "as_of": "2026-09-24",
            "fields": dict(MCP_CITATION.fields),
        },
    ]
    assert list(body["citations"][1]["fields"]) == [
        name for name, _ in MCP_CITATION.fields
    ], "field order survives serialization"
    assert body["tools_used"] == [], "no tool supplied evidence"


def test_tools_used_is_taken_from_the_result() -> None:
    response = to_query_response(
        QueryResult(
            status="answered",
            answer="Quote [T1].",
            citations=(MCP_CITATION,),
            tools_used=("get_market_quote",),
        )
    )

    assert json.loads(response.model_dump_json())["tools_used"] == ["get_market_quote"]


def test_the_citation_union_is_discriminated_and_closed() -> None:
    base = {"answer": "x", "status": "answered", "tools_used": []}
    mcp = {
        "id": "T1",
        "source_type": "mcp",
        "tool": "get_market_quote",
        "provider": "alpha_vantage",
        "symbol": "ACME",
        "as_of": "2026-09-24",
        "fields": {"price": "1"},
    }

    parsed = QueryResponse.model_validate({**base, "citations": [mcp]})
    assert isinstance(parsed.citations[0], McpQueryCitation)

    for broken in (
        {**mcp, "source_type": "web"},
        {**mcp, "tool": "fetch_url"},
        {**mcp, "provider": "other"},
        {**mcp, "excerpt": "prose"},
        {**mcp, "document_id": str(uuid4())},
    ):
        with pytest.raises(ValidationError):
            QueryResponse.model_validate({**base, "citations": [broken]})
    with pytest.raises(ValidationError):
        QueryResponse.model_validate({**base, "citations": [], "tools_used": ["x"]})


def test_the_public_tool_literals_are_exactly_the_mcp_allow_list() -> None:
    tool_type = McpQueryCitation.model_fields["tool"].annotation
    tools_used_type = QueryResponse.model_fields["tools_used"].annotation

    assert set(get_args(tool_type)) == ALLOWED_TOOLS
    (item_type,) = get_args(tools_used_type)
    assert set(get_args(item_type)) == ALLOWED_TOOLS


# ---------------------------------------------------------------------------
# Startup validation (AC9, AC14)
# ---------------------------------------------------------------------------


def _record_resources(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    created: list[object] = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:dbpass@127.0.0.1:1/x")
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    monkeypatch.setattr("app.main.create_pool", lambda config: created.append(config))
    monkeypatch.setattr(
        "app.main.create_openai_client", lambda config: created.append(config)
    )
    return created


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("RETRIEVAL_TOP_K", "0", "RETRIEVAL_TOP_K must be a positive integer"),
        ("RETRIEVAL_TOP_K", "six", "RETRIEVAL_TOP_K must be a positive integer"),
        (
            "MIN_RETRIEVAL_SIMILARITY",
            "1.5",
            "MIN_RETRIEVAL_SIMILARITY must be a number between 0 and 1",
        ),
        (
            "MIN_RETRIEVAL_SIMILARITY",
            "nan",
            "MIN_RETRIEVAL_SIMILARITY must be a number between 0 and 1",
        ),
    ],
)
def test_startup_refuses_invalid_retrieval_settings_before_any_resource(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    created = _record_resources(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError) as raised, TestClient(app):
        pass  # pragma: no cover

    assert str(raised.value) == message
    assert created == [], "no resource may be created before configuration passes"


@pytest.mark.parametrize("name", TRACING_ENV_VARS)
def test_startup_refuses_enabled_tracing_before_any_resource(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    created = _record_resources(monkeypatch)
    monkeypatch.setenv(name, "true")

    with pytest.raises(ConfigError) as raised, TestClient(app):
        pass  # pragma: no cover

    assert str(raised.value) == (
        f"{name} must be unset or disabled; LangSmith tracing is not supported"
    )
    assert created == [], "no resource may be created before configuration passes"


class _TracingProbe:
    def __init__(self) -> None:
        self.tracers = 0
        self.connections: list[object] = []


class _TracerConstructed(Exception):
    pass


def _clear_langsmith_env_cache() -> None:
    cached = langsmith.utils.get_env_var
    assert isinstance(cached, functools._lru_cache_wrapper)
    cached.cache_clear()


@pytest.fixture
def tracing_probe(monkeypatch: pytest.MonkeyPatch) -> Iterator[_TracingProbe]:
    """Spy on LangSmith tracer construction and refuse Python socket connects."""
    probe = _TracingProbe()

    def spy(self: object, *args: object, **kwargs: object) -> None:
        probe.tracers += 1
        raise _TracerConstructed

    def refuse(self: socket.socket, address: object) -> None:
        probe.connections.append(address)
        raise OSError("network access is refused in tests")

    monkeypatch.setattr(
        langchain_core.tracers.langchain.LangChainTracer, "__init__", spy
    )
    monkeypatch.setattr(socket.socket, "connect", refuse)
    _clear_langsmith_env_cache()
    try:
        yield probe
    finally:
        _clear_langsmith_env_cache()


@pytest.mark.parametrize("value", ["", "0", "false", "False"])
@pytest.mark.parametrize("name", TRACING_ENV_VARS)
def test_accepted_tracing_values_start_and_answer_without_a_tracer(
    live_env: str,
    tracing_probe: _TracingProbe,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    """The real lifespan starts, and a query runs the compiled graph through
    ``ainvoke`` over HTTP, for every exactly disabled value."""
    monkeypatch.setenv(name, value)
    answerer = ScriptedAnswerGenerator(grounded())

    def graph_factory(request: Request) -> QueryGraph:
        return fake_graph(FakeRetriever([evidence()]), answerer)

    for live in start_live(
        live_env, FakeEmbedder(), FakeTokenizer(), query_graph=graph_factory
    ):
        response = live.client.post(QUERY_URL, json={"question": QUESTION})

        assert response.status_code == 200
        assert response.json()["status"] == "answered"

    assert len(answerer.calls) == 1
    assert (tracing_probe.tracers, tracing_probe.connections) == (0, [])


# ---------------------------------------------------------------------------
# The vertical slice against PostgreSQL (AC1, AC10, AC11)
# ---------------------------------------------------------------------------


def _stored_chunk(database_url: str, chunk_id: str) -> tuple[UUID, str] | None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "SELECT document_id, content FROM document_chunks WHERE id = %s",
            (chunk_id,),
        ).fetchone()
    return None if row is None else (row[0], row[1])


def test_query_answers_from_an_ingested_document_with_a_verified_citation(
    live_env: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The Milestone 4 checkpoint: ingest, ask, retrieve, answer, verify."""
    answerer = ScriptedAnswerGenerator(
        GroundedAnswer(
            answer="Acme's European revenue declined 4% on currency headwinds [D1] [D9].",
            citation_ids=["D1", "D9", "D1"],
            insufficient_context=False,
        )
    )

    def graph_factory(request: Request) -> QueryGraph:
        retriever = Retriever(
            pool=request.app.state.pool,
            embedder=KeywordEmbedder(),
            config=RetrievalConfig(),
        )
        return build_query_graph(retriever=retriever, answerer=answerer)

    for live in start_live(
        live_env, KeywordEmbedder(), FakeTokenizer(), query_graph=graph_factory
    ):
        smoke = (Path(__file__).parent / "fixtures" / "smoke.txt").read_bytes()
        upload = live.client.post(
            URL, files={"file": ("acme-fy2025.txt", smoke, "text/plain")}
        )
        assert upload.status_code == 201
        document_id = upload.json()["document_id"]
        rows_before = live.counts()

        caplog.clear()
        caplog.set_level(logging.DEBUG)
        answered = live.client.post(QUERY_URL, json={"question": QUESTION})
        answered_events = events(caplog)

        assert answered.status_code == 200
        body = answered.json()
        assert (body["status"], body["tools_used"]) == ("answered", [])
        (citation,) = body["citations"]
        assert citation["id"] == "D1"
        assert citation["document_id"] == document_id
        assert (citation["filename"], citation["page"]) == ("acme-fy2025.txt", None)
        stored = _stored_chunk(live_env, citation["chunk_id"])
        assert stored is not None, "the chunk exists in the database"
        stored_content = stored[1]
        assert str(stored[0]) == document_id
        assert citation["excerpt"] in stored[1]
        assert "European revenue declined 4%" in citation["excerpt"]
        assert "D9" not in answered.text
        ((_, prompt),) = answerer.calls
        assert '<source id="D1"' in prompt
        assert stored[1] in prompt
        assert_logs_hold_none_of(
            caplog, QUESTION, stored_content, prompt, "currency headwinds", DUMMY_KEY
        )

        caplog.clear()
        unrelated = live.client.post(
            QUERY_URL, json={"question": "What is Initech's dividend policy?"}
        )
        unrelated_events = events(caplog)

        assert unrelated.status_code == 200
        assert unrelated.json() == INSUFFICIENT_BODY
        assert len(answerer.calls) == 1
        assert live.counts() == rows_before, "a query is read-only"
        assert_logs_hold_none_of(caplog, DUMMY_KEY)

    # One request ID per query, shared by every event of that query.
    for query_events in (answered_events, unrelated_events):
        ids = {event.get("request_id") for event in query_events}
        assert len(ids) == 1 and None not in ids
    assert answered_events[0]["request_id"] != unrelated_events[0]["request_id"]
    kinds = {event["event"] for event in answered_events}
    assert {"graph.started", "retrieval.completed", "graph.completed"} <= kinds
    assert "citation.unknown_id" in kinds


# ===========================================================================
# Milestone 6: the optional MCP client in the lifespan, and RAG+MCP over HTTP
# ===========================================================================

AV_SENTINEL = "AV-SENTINEL-KEY-7f3a"
AV_URL = f"https://www.alphavantage.co/query?apikey={AV_SENTINEL}"
MCP_EVENT_FIELDS = {"event", "outcome"}


class InProcessOpener:
    """Replaces ``app.main.open_market_tools``: the real client over an
    in-process server, so no child is started and no socket is opened."""

    def __init__(self, provider: object) -> None:
        self.provider = provider
        self.configs: list[MarketDataConfig] = []
        self.opened: list[MarketDataTools] = []
        self.exits = 0

    @asynccontextmanager
    async def open(self, config: MarketDataConfig) -> AsyncIterator[MarketDataTools]:
        self.configs.append(config)
        server = build_mcp_server(cast(MarketDataProvider, self.provider))
        try:
            async with open_market_data_tools(
                server, timeout_seconds=config.timeout_seconds
            ) as tools:
                self.opened.append(tools)
                yield tools
        finally:
            self.exits += 1


def refusing_opener(
    calls: list[MarketDataConfig],
) -> Callable[[MarketDataConfig], AbstractAsyncContextManager[MarketDataTools]]:
    """An opener that records a call and fails the test if it is entered."""

    @asynccontextmanager
    async def opener(config: MarketDataConfig) -> AsyncIterator[MarketDataTools]:
        calls.append(config)
        raise AssertionError("no MCP child may be started")
        yield  # pragma: no cover

    return opener


def failing_opener(
    calls: list[MarketDataConfig],
) -> Callable[[MarketDataConfig], AbstractAsyncContextManager[MarketDataTools]]:
    """An opener whose entry fails as a child that exits at once would, with
    a secret and a URL in the member text (P4)."""

    @asynccontextmanager
    async def opener(config: MarketDataConfig) -> AsyncIterator[MarketDataTools]:
        calls.append(config)
        raise ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError(f"child failed: {AV_SENTINEL} {AV_URL}")],
        )
        yield  # pragma: no cover

    return opener


def capture_graph_kwargs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every ``build_query_graph`` call the lifespan makes."""
    captured: list[dict[str, Any]] = []
    real = build_query_graph

    def recording(**kwargs: Any) -> QueryGraph:
        captured.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("app.main.build_query_graph", recording)
    return captured


def mcp_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        event
        for event in events(caplog)
        if event["event"] in {"mcp.startup", "mcp.shutdown"}
    ]


def tool_graph(
    retriever_for: Callable[[Request], QueryRetriever],
    answerer: ScriptedAnswerGenerator,
    planner: ScriptedToolPlanner,
) -> Callable[[Request], QueryGraph]:
    """A ``get_query_graph`` override: the real graph, compiled once, over
    scripted models and the lifespan-owned MCP client.

    FastAPI runs this synchronous dependency in a worker thread, so the
    one-time compilation is guarded for concurrent first requests.
    """
    compiled: list[QueryGraph] = []
    lock = threading.Lock()

    def factory(request: Request) -> QueryGraph:
        with lock:
            if not compiled:
                compiled.append(
                    build_query_graph(
                        retriever=retriever_for(request),
                        answerer=answerer,
                        planner=planner,
                        market_tools=request.app.state.market_tools,
                    )
                )
            return compiled[0]

    return factory


def fixed(retriever: QueryRetriever) -> Callable[[Request], QueryRetriever]:
    return lambda request: retriever


def pooled_retriever(request: Request) -> QueryRetriever:
    return Retriever(
        pool=request.app.state.pool,
        embedder=KeywordEmbedder(),
        config=RetrievalConfig(),
    )


def ask_with_tools(client: TestClient, question: str) -> httpx2.Response:
    return client.post(QUERY_URL, json={"question": question, "use_tools": True})


def plan(tool: str | None, symbol: str | None) -> ToolPlan:
    return ToolPlan.model_validate({"tool_name": tool, "symbol": symbol})


# ---------------------------------------------------------------------------
# Startup modes (T22-T25; AC13-AC15, AC17, AC18)
# ---------------------------------------------------------------------------


def test_available_mode_enters_one_shared_client_and_closes_it(
    live_env: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", f" {AV_SENTINEL} ")
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", "7.5")
    opener = InProcessOpener(ScriptedMarketDataProvider())
    monkeypatch.setattr("app.main.open_market_tools", opener.open)
    captured = capture_graph_kwargs(monkeypatch)
    caplog.set_level(logging.DEBUG)

    with TestClient(app) as client:
        (tools,) = opener.opened
        assert app.state.market_tools is tools
        ((kwargs),) = captured
        assert kwargs["market_tools"] is tools
        assert isinstance(kwargs["planner"], OpenAIToolPlanner)
        assert opener.exits == 0
        assert client.get("/health").status_code == 200

    assert opener.configs == [
        MarketDataConfig(api_key=AV_SENTINEL, timeout_seconds=7.5)
    ]
    assert opener.exits == 1
    assert mcp_events(caplog) == [
        {"event": "mcp.startup", "outcome": "available"},
        {"event": "mcp.shutdown", "outcome": "closed"},
    ]
    assert_logs_hold_none_of(caplog, AV_SENTINEL, DUMMY_KEY, "postgresql://")


def test_without_a_key_no_child_starts_and_use_tools_calls_nothing(
    live_env: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    opener_calls: list[MarketDataConfig] = []
    monkeypatch.setattr("app.main.open_market_tools", refusing_opener(opener_calls))
    captured = capture_graph_kwargs(monkeypatch)
    planner = ScriptedToolPlanner()
    answerer = ScriptedAnswerGenerator(grounded())
    caplog.set_level(logging.DEBUG)

    for live in start_live(
        live_env,
        FakeEmbedder(),
        FakeTokenizer(),
        query_graph=tool_graph(fixed(FakeRetriever([evidence()])), answerer, planner),
    ):
        assert app.state.market_tools is None
        assert captured[0]["market_tools"] is None
        assert live.client.get("/health").status_code == 200
        response = live.client.post(
            QUERY_URL, json={"question": QUESTION, "use_tools": True}
        )
        assert response.status_code == 200
        assert (response.json()["status"], response.json()["tools_used"]) == (
            "answered",
            [],
        )

    assert opener_calls == []
    assert planner.calls == []
    assert mcp_events(caplog) == [{"event": "mcp.startup", "outcome": "not_configured"}]
    (route,) = [e for e in named(caplog, "graph.route") if e["node"] == "retrieve"]
    assert route["tools_available"] is False


def test_a_child_that_cannot_start_degrades_to_rag_only(
    live_env: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SENTINEL)
    opener_calls: list[MarketDataConfig] = []
    monkeypatch.setattr("app.main.open_market_tools", failing_opener(opener_calls))
    planner = ScriptedToolPlanner()
    answerer = ScriptedAnswerGenerator(grounded())
    caplog.set_level(logging.DEBUG)

    for live in start_live(
        live_env,
        FakeEmbedder(),
        FakeTokenizer(),
        query_graph=tool_graph(fixed(FakeRetriever([evidence()])), answerer, planner),
    ):
        assert app.state.market_tools is None
        assert live.client.get("/health").status_code == 200
        response = live.client.post(
            QUERY_URL, json={"question": QUESTION, "use_tools": True}
        )
        assert response.status_code == 200
        assert response.json()["tools_used"] == []

    assert len(opener_calls) == 1
    assert planner.calls == []
    assert mcp_events(caplog) == [{"event": "mcp.startup", "outcome": "start_failed"}]
    (record,) = [r for r in caplog.records if '"mcp.startup"' in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert_logs_hold_none_of(
        caplog,
        AV_SENTINEL,
        AV_URL,
        "alphavantage",
        "child failed",
        "ExceptionGroup",
        "RuntimeError",
        DUMMY_KEY,
    )


@pytest.mark.parametrize("key", [None, AV_SENTINEL], ids=["without-key", "with-key"])
@pytest.mark.parametrize("value", ["0", "31", "nan", "five"])
def test_startup_refuses_an_invalid_mcp_timeout_before_any_resource(
    monkeypatch: pytest.MonkeyPatch, key: str | None, value: str
) -> None:
    created = _record_resources(monkeypatch)
    opener_calls: list[MarketDataConfig] = []
    monkeypatch.setattr("app.main.open_market_tools", refusing_opener(opener_calls))
    if key is not None:
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", key)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigError) as raised, TestClient(app):
        pass  # pragma: no cover

    assert str(raised.value) == (
        "MCP_TOOL_TIMEOUT_SECONDS must be a number greater than 0 and at most 30"
    )
    assert created == [], "no resource may be created before configuration passes"
    assert opener_calls == []


# ---------------------------------------------------------------------------
# Exit order, partial startup, and contained close failure (T26; AC13)
# ---------------------------------------------------------------------------


class _RecordingPool:
    def __init__(self, order: list[str]) -> None:
        self.order = order

    async def open(self) -> None:
        self.order.append("pool.open")

    async def close(self) -> None:
        self.order.append("pool.close")


def recording_resources(
    monkeypatch: pytest.MonkeyPatch, *, close_error: Exception | None = None
) -> list[str]:
    """Replace the pool, OpenAI client, and MCP opener with recording fakes."""
    order: list[str] = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://app:dbpass@127.0.0.1:1/x")
    monkeypatch.setenv("OPENAI_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SENTINEL)

    class RecordingOpenAI(AsyncOpenAI):
        async def close(self) -> None:
            order.append("openai.close")
            await super().close()

    def create_openai_client(config: object) -> AsyncOpenAI:
        order.append("openai.create")
        return RecordingOpenAI(api_key=DUMMY_KEY, base_url="http://openai.invalid/v1")

    @asynccontextmanager
    async def opener(config: MarketDataConfig) -> AsyncIterator[MarketDataTools]:
        order.append("mcp.enter")
        yield cast(MarketDataTools, object())
        order.append("mcp.close")
        if close_error is not None:
            raise close_error

    monkeypatch.setattr("app.main.create_pool", lambda config: _RecordingPool(order))
    monkeypatch.setattr("app.main.create_openai_client", create_openai_client)
    monkeypatch.setattr("app.main.open_market_tools", opener)
    return order


STARTUP_ORDER = ["pool.open", "openai.create", "mcp.enter"]
SHUTDOWN_ORDER = ["mcp.close", "openai.close", "pool.close"]


def test_resources_close_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    order = recording_resources(monkeypatch)
    caplog.set_level(logging.DEBUG)

    with TestClient(app):
        assert order == STARTUP_ORDER

    assert order == STARTUP_ORDER + SHUTDOWN_ORDER
    assert mcp_events(caplog)[-1] == {"event": "mcp.shutdown", "outcome": "closed"}


def test_a_failure_after_startup_unwinds_every_entered_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order = recording_resources(monkeypatch)

    def broken_graph(**kwargs: object) -> NoReturn:
        raise RuntimeError("graph compilation defect")

    monkeypatch.setattr("app.main.build_query_graph", broken_graph)

    with pytest.raises(RuntimeError, match="graph compilation defect"), TestClient(app):
        pass  # pragma: no cover

    assert order == STARTUP_ORDER + SHUTDOWN_ORDER


def test_an_mcp_close_failure_is_contained_and_shutdown_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    order = recording_resources(
        monkeypatch, close_error=RuntimeError(f"close failed: {AV_SENTINEL} {AV_URL}")
    )
    caplog.set_level(logging.DEBUG)

    with TestClient(app):
        pass

    assert order == STARTUP_ORDER + SHUTDOWN_ORDER
    assert mcp_events(caplog) == [
        {"event": "mcp.startup", "outcome": "available"},
        {"event": "mcp.shutdown", "outcome": "close_failed"},
    ]
    assert_logs_hold_none_of(caplog, AV_SENTINEL, AV_URL, "close failed")


@pytest.mark.anyio
async def test_cancellation_while_entering_the_client_propagates(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @asynccontextmanager
    async def cancelled(config: MarketDataConfig) -> AsyncIterator[MarketDataTools]:
        raise asyncio.CancelledError
        yield  # pragma: no cover

    monkeypatch.setattr("app.main.open_market_tools", cancelled)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(asyncio.CancelledError):
        async with optional_market_tools(MarketDataConfig(api_key=AV_SENTINEL)):
            pass  # pragma: no cover

    assert mcp_events(caplog) == []


@pytest.mark.anyio
async def test_cancellation_while_closing_the_client_propagates(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @asynccontextmanager
    async def opener(config: MarketDataConfig) -> AsyncIterator[MarketDataTools]:
        yield cast(MarketDataTools, object())
        raise asyncio.CancelledError

    monkeypatch.setattr("app.main.open_market_tools", opener)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(asyncio.CancelledError):
        async with optional_market_tools(MarketDataConfig(api_key=AV_SENTINEL)):
            pass

    assert mcp_events(caplog) == [{"event": "mcp.startup", "outcome": "available"}]


# ---------------------------------------------------------------------------
# The stdio launch seam (T27; AC19)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_open_market_tools_launches_the_server_with_stderr_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[object, float, TextIO | None]] = []
    sentinel_tools = cast(MarketDataTools, object())

    @asynccontextmanager
    async def recording(
        server: object, *, timeout_seconds: float, errlog: TextIO | None = None
    ) -> AsyncIterator[MarketDataTools]:
        seen.append((server, timeout_seconds, errlog))
        yield sentinel_tools

    monkeypatch.setattr("app.main.open_market_data_tools", recording)
    config = MarketDataConfig(api_key=AV_SENTINEL, timeout_seconds=7.5)

    async with open_market_tools(config) as tools:
        assert tools is sentinel_tools
        ((server, timeout_seconds, errlog),) = seen
        assert errlog is not None
        assert not errlog.closed and errlog.writable()

    assert server == stdio_server_parameters(config)
    assert timeout_seconds == 7.5
    assert errlog.name == os.devnull
    assert errlog.closed, "the sink is closed with the client"


# ---------------------------------------------------------------------------
# Shared-client concurrency through the lifespan (T21; AC16)
# ---------------------------------------------------------------------------


def test_concurrent_requests_share_the_lifespan_owned_client(
    live_env: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four ``use_tools`` queries overlap on the one lifespan-owned client.

    The provider holds every call until all four are in flight, so requests
    that were serialized, or served by separate clients that never met,
    could not all pass. The planner is keyed by question, so each response's
    ``T1`` must carry the symbol its own question asked for.
    """
    parties = 4
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    questions = {symbol: f"What is the latest {symbol} quote?" for symbol in symbols}
    provider = BarrierMarketDataProvider(parties, deadline_seconds=5.0)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SENTINEL)
    opener = InProcessOpener(provider)
    monkeypatch.setattr("app.main.open_market_tools", opener.open)
    planner = ScriptedToolPlanner(
        by_prompt={
            render_tool_plan_input(question): plan("get_market_quote", symbol.lower())
            for symbol, question in questions.items()
        }
    )
    answerer = ScriptedAnswerGenerator(
        *[grounded("The latest quote [T1].", ["T1"]) for _ in symbols]
    )
    graph = tool_graph(fixed(FakeRetriever([])), answerer, planner)

    for live in start_live(
        live_env, FakeEmbedder(), FakeTokenizer(), query_graph=graph
    ):
        ask = functools.partial(ask_with_tools, live.client)
        with ThreadPoolExecutor(max_workers=parties) as pool:
            answers = pool.map(ask, [questions[symbol] for symbol in symbols])
            responses = dict(zip(symbols, answers, strict=True))

    assert len(opener.opened) == 1, "one client for every request"
    assert provider.barrier_reached
    assert provider.max_active == parties
    for symbol, response in responses.items():
        assert response.status_code == 200
        body = response.json()
        assert (body["status"], body["tools_used"]) == (
            "answered",
            ["get_market_quote"],
        )
        (citation,) = body["citations"]
        assert (citation["id"], citation["symbol"]) == ("T1", symbol)
    assert len(planner.calls) == parties


# ---------------------------------------------------------------------------
# RAG-only and RAG+MCP through one endpoint (T29; AC1, AC12, T30)
# ---------------------------------------------------------------------------

TOOL_QUESTION = (
    "Why did Acme's European revenue decline, and what is the latest ACME quote?"
)
RAW_PLANNED_SYMBOL = " AcMe "


def assert_stored_document_citation(
    database_url: str, citation: dict[str, Any], document_id: str
) -> None:
    """A ``D`` citation that points at the stored chunk it quotes."""
    assert (citation["id"], citation["source_type"]) == ("D1", "document")
    assert citation["document_id"] == document_id
    stored = _stored_chunk(database_url, citation["chunk_id"])
    assert stored is not None and str(stored[0]) == document_id
    assert citation["excerpt"] in stored[1]


def expected_quote_citation(quote: MarketQuote) -> dict[str, Any]:
    """The D13 public citation for a quote: application-built, in field order."""
    return {
        "id": "T1",
        "source_type": "mcp",
        "tool": "get_market_quote",
        "provider": "alpha_vantage",
        "symbol": quote.symbol,
        "as_of": quote.latest_trading_day,
        "fields": {
            "price": quote.price,
            "previous_close": quote.previous_close,
            "change": quote.change,
            "change_percent": quote.change_percent,
            "volume": quote.volume,
            "latest_trading_day": quote.latest_trading_day,
        },
    }


def test_rag_only_and_rag_with_mcp_through_the_same_endpoint(
    live_env: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The Milestone 6 exit condition, against PostgreSQL and a real in-process
    MCP server behind the lifespan-owned client."""
    acme_quote = quote_for("ACME")
    provider = ScriptedMarketDataProvider(quote=acme_quote)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SENTINEL)
    opener = InProcessOpener(provider)
    monkeypatch.setattr("app.main.open_market_tools", opener.open)
    planner = ScriptedToolPlanner(
        plan("get_market_quote", RAW_PLANNED_SYMBOL),
        plan("get_market_quote", RAW_PLANNED_SYMBOL),
    )
    answerer = ScriptedAnswerGenerator(
        grounded("European revenue declined 4% [D1].", ["D1"]),
        grounded(
            "Revenue declined 4% [D1]; ACME last traded at 123.45 [T1].", ["D1", "T1"]
        ),
        grounded("Revenue declined 4% [D1].", ["D1"]),
    )
    caplog.set_level(logging.DEBUG)

    for live in start_live(
        live_env,
        KeywordEmbedder(),
        FakeTokenizer(),
        query_graph=tool_graph(pooled_retriever, answerer, planner),
    ):
        smoke = (Path(__file__).parent / "fixtures" / "smoke.txt").read_bytes()
        upload = live.client.post(
            URL, files={"file": ("acme-fy2025.txt", smoke, "text/plain")}
        )
        assert upload.status_code == 201
        document_id = upload.json()["document_id"]
        rows_before = live.counts()

        # 1. RAG only: use_tools=false never plans or calls a tool.
        rag = live.client.post(
            QUERY_URL, json={"question": QUESTION, "use_tools": False}
        ).json()
        assert (rag["status"], rag["tools_used"]) == ("answered", [])
        (rag_citation,) = rag["citations"]
        assert_stored_document_citation(live_env, rag_citation, document_id)
        assert (planner.calls, provider.calls) == ([], [])

        # 2. RAG + MCP: one planned, validated call becomes T1.
        both = ask_with_tools(live.client, TOOL_QUESTION)
        assert both.status_code == 200
        body = both.json()
        assert (body["status"], body["tools_used"]) == (
            "answered",
            ["get_market_quote"],
        )
        document, market = body["citations"]
        assert_stored_document_citation(live_env, document, document_id)
        assert market == expected_quote_citation(acme_quote)
        assert list(market["fields"]) == list(
            expected_quote_citation(acme_quote)["fields"]
        )
        assert provider.calls == [("get_quote", "ACME")], "one normalized call"
        assert '<source id="T1" type="mcp">' in answerer.calls[1][1]

        # 3. A tool failure falls back to document evidence only.
        provider.quote = MarketDataError("rate_limited")
        fallback = ask_with_tools(live.client, TOOL_QUESTION)
        assert fallback.status_code == 200
        fallback_body = fallback.json()
        assert (fallback_body["status"], fallback_body["tools_used"]) == (
            "answered",
            [],
        )
        assert [c["id"] for c in fallback_body["citations"]] == ["D1"]
        assert len(provider.calls) == 2
        assert '<source id="T1"' not in answerer.calls[2][1]
        assert "rate_limited" not in answerer.calls[2][1]
        assert "rate_limited" not in fallback.text

        assert live.counts() == rows_before, "queries are read-only"
        assert len(planner.calls) == 2

    assert len(opener.opened) == 1
    failed = named(caplog, "mcp.tool.failed")
    assert [e["error_code"] for e in failed] == ["rate_limited"]
    completed = named(caplog, "graph.completed")
    assert [(e["tool_used"], e["tool_error"]) for e in completed] == [
        (None, None),
        ("get_market_quote", None),
        (None, "rate_limited"),
    ]
    # The in-process server's own SDK logger records the wire error; in
    # production that logger lives in the child, whose stderr is discarded.
    api_logged = "\n".join(
        record.getMessage()
        for record in caplog.records
        if not record.name.startswith("mcp.server")
    )
    assert "Error executing tool" not in api_logged
    assert_logs_hold_none_of(
        caplog,
        DUMMY_KEY,
        AV_SENTINEL,
        "postgresql://",
        RAW_PLANNED_SYMBOL.strip(),
        QUESTION,
        TOOL_QUESTION,
        acme_quote.price,
    )
