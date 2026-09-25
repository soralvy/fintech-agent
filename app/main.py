"""FastAPI application entrypoint.

Lifespan owns the shared resources and compiles the query graph once; route
handlers stay thin (docs/DECISIONS.md section 3.1). Every error leaves in the
SPEC section 12.1 envelope with a fixed message, and an unexpected exception
is turned into the ``500`` envelope by ``UnexpectedErrorMiddleware`` instead
of reaching the server's own error logging (docs/DECISIONS.md section 13).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from python_multipart.exceptions import FormParserError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.citations import Citation, McpCitation
from app.config import (
    DatabaseConfig,
    IngestionConfig,
    OpenAIConfig,
    RetrievalConfig,
    require_tracing_disabled,
)
from app.db import Pool, check_database, create_pool
from app.errors import (
    AppError,
    DatabaseUnavailableError,
    InvalidRequestError,
    UploadTooLargeError,
    classify_error,
)
from app.graph import QueryGraph, QueryResult, build_query_graph, run_query
from app.ingestion import Ingestor
from app.logging import bind_request_id, configure_logging, log_event
from app.openai_provider import (
    OpenAIAnswerGenerator,
    OpenAIEmbedder,
    create_openai_client,
)
from app.retrieval import Retriever
from app.schemas import (
    DocumentIngestResponse,
    ErrorDetail,
    ErrorResponse,
    QueryRequest,
    QueryResponse,
)
from app.tokenizer import TiktokenTokenizer

logger = logging.getLogger(__name__)

# One generic message for every request FastAPI itself cannot validate or
# parse, on any route. It never echoes the submitted input.
REQUEST_INVALID_CODE = "invalid_request"
REQUEST_INVALID_MESSAGE = "The request is malformed or failed validation."

# Allowance for multipart boundaries and part headers on top of the file itself
# when rejecting an oversized upload from its Content-Length alone.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the pool and the OpenAI client, and compile the query graph once.

    All configuration is read first, including the LangSmith tracing refusal,
    so a missing or invalid setting stops startup before any resource is
    created. The tokenizer loads its encoding on first use, never here, so
    startup makes no network request.
    """
    require_tracing_disabled()
    database_config = DatabaseConfig.from_env()
    openai_config = OpenAIConfig.from_env()
    ingestion_config = IngestionConfig.from_env()
    retrieval_config = RetrievalConfig.from_env()
    configure_logging()

    pool = create_pool(database_config)
    await pool.open()
    try:
        async with create_openai_client(openai_config) as openai_client:
            embedder = OpenAIEmbedder(
                openai_client,
                model=openai_config.embedding_model,
                dimensions=openai_config.embedding_dimensions,
            )
            app.state.pool = pool
            app.state.ingestor = Ingestor(
                pool=pool,
                embedder=embedder,
                tokenizer=TiktokenTokenizer(),
                config=ingestion_config,
            )
            app.state.query_graph = build_query_graph(
                retriever=Retriever(
                    pool=pool, embedder=embedder, config=retrieval_config
                ),
                answerer=OpenAIAnswerGenerator(
                    openai_client, model=openai_config.llm_model
                ),
            )
            yield
    finally:
        await pool.close()


class UnexpectedErrorMiddleware:
    """Bind a request ID, and turn an unexpected exception into the ``500`` envelope.

    Added with ``app.add_middleware``, so it runs inside Starlette's
    ``ServerErrorMiddleware`` and outside ``ExceptionMiddleware``: errors with
    a registered handler never reach it, and an ``Exception`` it catches never
    reaches the server, which would otherwise log its message and traceback.
    It logs one ``http.request.failed`` event with only bounded fields and
    never re-raises. Non-HTTP scopes, such as ``lifespan``, pass through, and
    a ``BaseException`` that is not an ``Exception`` propagates unchanged.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        with bind_request_id(uuid4().hex):
            try:
                await self.app(scope, receive, tracking_send)
            # A blind catch is the point: D24 requires every Exception to end
            # here, with no re-raise and no exc_info, which are the only two
            # forms BLE001 accepts. BaseException still propagates.
            except Exception as exc:  # noqa: BLE001
                log_event(
                    logger,
                    "http.request.failed",
                    level=logging.ERROR,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_code=AppError.code,
                    error_type=classify_error(exc),
                )
                if response_started:
                    return
                response = _error_response(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    AppError.code,
                    AppError.message,
                )
                await response(scope, receive, send)


app = FastAPI(title="FinTech Research Agent", lifespan=lifespan)
app.add_middleware(UnexpectedErrorMiddleware)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=body.model_dump())


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Render an application error as the SPEC section 12.1 envelope."""
    return _error_response(exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Any FastAPI validation failure, on any route, without echoing the input."""
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        REQUEST_INVALID_CODE,
        REQUEST_INVALID_MESSAGE,
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """Map FastAPI's own body-parse failure to the validation envelope.

    FastAPI raises ``HTTPException(400)`` when ``Request.json()`` fails with
    anything but a ``JSONDecodeError``, such as a non-UTF-8 body. Every other
    status keeps FastAPI's default ``{"detail": ...}`` rendering, including
    the ``/health`` 503 and framework 404/405 responses.
    """
    if exc.status_code == status.HTTP_400_BAD_REQUEST:
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            REQUEST_INVALID_CODE,
            REQUEST_INVALID_MESSAGE,
        )
    return await http_exception_handler(request, exc)


def get_pool(request: Request) -> Pool:
    """Hand the pooled database to a route."""
    pool: Pool = request.app.state.pool
    return pool


def get_ingestor(request: Request) -> Ingestor:
    """Hand the lifespan-built ingestion service to a route."""
    ingestor: Ingestor = request.app.state.ingestor
    return ingestor


def get_query_graph(request: Request) -> QueryGraph:
    """Hand the graph compiled once in the lifespan to a route."""
    graph: QueryGraph = request.app.state.query_graph
    return graph


class HealthResponse(BaseModel):
    """Public payload of ``GET /health``."""

    status: Literal["ok"]
    database: Literal["ok"]


@app.get("/health")
async def health(pool: Annotated[Pool, Depends(get_pool)]) -> HealthResponse:
    """Report that the process is serving and the database answers a query.

    OpenAI and the market-data provider are deliberately not checked
    (docs/SPEC.md section 6.1).
    """
    try:
        await check_database(pool)
    except DatabaseUnavailableError:
        # Keeps the ``detail`` shape until Milestone 7 (docs/DECISIONS.md
        # section 13); db.py has already dropped the driver's message.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="database unavailable",
        ) from None
    return HealthResponse(status="ok", database="ok")


_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorResponse} for code in (400, 413, 415, 422, 502, 503)
}

# The body is parsed in the handler rather than declared as an ``UploadFile``
# parameter: FastAPI would otherwise parse the whole multipart body before any
# check runs, and render its own validation errors outside the SPEC envelope.
_UPLOAD_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {"file": {"type": "string", "format": "binary"}},
            }
        }
    },
}


@app.post(
    "/v1/documents",
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": DocumentIngestResponse,
            "description": "Already ingested",
        },
        **_ERROR_RESPONSES,
    },
    openapi_extra={"requestBody": _UPLOAD_REQUEST_BODY},
)
async def ingest_document(
    request: Request,
    response: Response,
    ingestor: Annotated[Ingestor, Depends(get_ingestor)],
) -> DocumentIngestResponse:
    """Ingest one PDF, Markdown, or text file synchronously (docs/SPEC.md section 6.2)."""
    max_bytes = ingestor.max_upload_bytes
    declared_length = request.headers.get("content-length", "")
    if (
        declared_length.isdigit()
        and int(declared_length) > max_bytes + MULTIPART_OVERHEAD_BYTES
    ):
        raise UploadTooLargeError

    try:
        form = await request.form(max_files=1, max_fields=0)
    except (StarletteHTTPException, FormParserError):
        # Starlette raises a 400 for too many files or any extra field, but
        # lets python-multipart's own error escape for a malformed body.
        raise InvalidRequestError from None
    try:
        uploads = form.getlist("file")
        if len(uploads) != 1 or not isinstance(uploads[0], UploadFile):
            raise InvalidRequestError
        result = await ingestor.ingest(uploads[0], request_id=uuid4().hex)
    finally:
        await form.close()

    if result.status == "already_ingested":
        response.status_code = status.HTTP_200_OK
    return DocumentIngestResponse(
        document_id=result.document_id,
        filename=result.filename,
        sha256=result.sha256,
        page_count=result.page_count,
        chunk_count=result.chunk_count,
        status=result.status,
    )


@app.post(
    "/v1/query",
    responses={code: {"model": ErrorResponse} for code in (422, 502, 503)},
)
async def query(
    body: QueryRequest,
    graph: Annotated[QueryGraph, Depends(get_query_graph)],
) -> QueryResponse:
    """Answer a question from the ingested documents (docs/SPEC.md section 6.3).

    Insufficient context is a ``200``. ``use_tools`` is accepted but makes no
    MCP call until Milestone 6, so ``tools_used`` is always empty.
    """
    result = await run_query(graph, question=body.question, use_tools=body.use_tools)
    return to_query_response(result)


def to_query_response(result: QueryResult) -> QueryResponse:
    """Convert the graph's validated result into the public schema."""
    return QueryResponse.model_validate(
        {
            "answer": result.answer,
            "status": result.status,
            "citations": [_citation_payload(citation) for citation in result.citations],
            "tools_used": [],
        }
    )


def _citation_payload(citation: Citation) -> dict[str, object]:
    """One trusted citation as its public shape, keeping MCP field order."""
    if isinstance(citation, McpCitation):
        return {
            "id": citation.id,
            "source_type": citation.source_type,
            "tool": citation.tool,
            "provider": citation.provider,
            "symbol": citation.symbol,
            "as_of": citation.as_of,
            "fields": dict(citation.fields),
        }
    return {
        "id": citation.id,
        "source_type": citation.source_type,
        "document_id": citation.document_id,
        "chunk_id": citation.chunk_id,
        "filename": citation.filename,
        "page": citation.page,
        "excerpt": citation.excerpt,
    }
