"""FastAPI application entrypoint.

Lifespan owns the shared resources; route handlers stay thin
(docs/DECISIONS.md section 3.1).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from python_multipart.exceptions import FormParserError
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import DatabaseConfig, IngestionConfig, OpenAIConfig
from app.db import Pool, check_database, create_pool
from app.errors import (
    AppError,
    DatabaseUnavailableError,
    InvalidRequestError,
    UploadTooLargeError,
)
from app.ingestion import Ingestor
from app.logging import configure_logging
from app.openai_provider import OpenAIEmbedder, create_openai_client
from app.schemas import DocumentIngestResponse, ErrorDetail, ErrorResponse
from app.tokenizer import TiktokenTokenizer

# Allowance for multipart boundaries and part headers on top of the file itself
# when rejecting an oversized upload from its Content-Length alone.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the pool and the OpenAI client for the life of the application.

    All configuration is read first, so a missing or invalid setting stops
    startup before any resource is created. The tokenizer loads its encoding
    on first use, never here, so startup makes no network request.
    """
    database_config = DatabaseConfig.from_env()
    openai_config = OpenAIConfig.from_env()
    ingestion_config = IngestionConfig.from_env()
    configure_logging()

    pool = create_pool(database_config)
    await pool.open()
    try:
        async with create_openai_client(openai_config) as openai_client:
            app.state.pool = pool
            app.state.ingestor = Ingestor(
                pool=pool,
                embedder=OpenAIEmbedder(
                    openai_client,
                    model=openai_config.embedding_model,
                    dimensions=openai_config.embedding_dimensions,
                ),
                tokenizer=TiktokenTokenizer(),
                config=ingestion_config,
            )
            yield
    finally:
        await pool.close()


app = FastAPI(title="FinTech Research Agent", lifespan=lifespan)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Render an application error as the SPEC section 12.1 envelope."""
    body = ErrorResponse(error=ErrorDetail(code=exc.code, message=exc.message))
    return JSONResponse(status_code=exc.status_code, content=body.model_dump())


def get_pool(request: Request) -> Pool:
    """Hand the pooled database to a route."""
    pool: Pool = request.app.state.pool
    return pool


def get_ingestor(request: Request) -> Ingestor:
    """Hand the lifespan-built ingestion service to a route."""
    ingestor: Ingestor = request.app.state.ingestor
    return ingestor


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
