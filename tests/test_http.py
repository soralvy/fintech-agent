"""HTTP contract tests for ``POST /v1/documents`` and application startup.

Validation and error-mapping tests run without a database: they either fail
before the database is reached or use a pool that refuses to connect. Tests
that store documents run the real lifespan (``TestClient`` as a context
manager) against the test database, with the embedder and tokenizer replaced
by deterministic fakes. No test calls OpenAI or loads tiktoken encoding data.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import NoReturn, cast

import httpx
import psycopg
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app.config import ConfigError, IngestionConfig
from app.db import Pool
from app.errors import EmbeddingProviderError
from app.ingestion import Ingestor
from app.main import app, get_ingestor
from app.tokenizer import TiktokenTokenizer, Tokenizer
from tests.db_safety import reset_test_database
from tests.fakes import FakeEmbedder, FakeTokenizer, build_pdf

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
class LiveApp:
    client: TestClient
    embedder: FakeEmbedder
    database_url: str

    def counts(self) -> tuple[int, int]:
        with psycopg.connect(self.database_url) as conn:
            documents = conn.execute("SELECT count(*) FROM documents").fetchone()
            chunks = conn.execute("SELECT count(*) FROM document_chunks").fetchone()
        assert documents is not None and chunks is not None
        return documents[0], chunks[0]


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


def start_live(
    database_url: str, embedder: FakeEmbedder, tokenizer: Tokenizer
) -> Iterator[LiveApp]:
    def fake_ingestor(request: Request) -> Ingestor:
        return Ingestor(
            pool=request.app.state.pool,
            embedder=embedder,
            tokenizer=tokenizer,
            config=IngestionConfig(max_upload_bytes=UPLOAD_LIMIT * 16),
        )

    app.dependency_overrides[get_ingestor] = fake_ingestor
    try:
        with TestClient(app) as client:
            yield LiveApp(client, embedder, database_url)
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
