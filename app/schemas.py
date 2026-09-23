"""Public HTTP schemas (docs/SPEC.md sections 6 and 12.1)."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class DocumentIngestResponse(BaseModel):
    """Body of ``POST /v1/documents`` for both 201 and the 200 duplicate."""

    document_id: UUID
    filename: str
    sha256: str
    page_count: int | None
    chunk_count: int
    status: Literal["ingested", "already_ingested"]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    """The SPEC section 12.1 error envelope."""

    error: ErrorDetail
