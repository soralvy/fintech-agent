"""Public HTTP schemas (docs/SPEC.md sections 6 and 12.1).

HTTP request and response models only. Structured model outputs live beside
the adapter that validates them (docs/DECISIONS.md section 4).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints

# The same bounds as the graph's own boundary check (``app.graph``). Both
# boundaries validate independently, and schemas does not import the graph.
QUESTION_MIN_CHARS = 3
QUESTION_MAX_CHARS = 2000


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


class QueryRequest(BaseModel):
    """Body of ``POST /v1/query`` (docs/SPEC.md section 6.3).

    The question is trimmed before its length is checked. ``use_tools`` must
    be a JSON boolean, and unknown fields are rejected (docs/DECISIONS.md
    section 13).
    """

    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=QUESTION_MIN_CHARS,
            max_length=QUESTION_MAX_CHARS,
        ),
    ]
    use_tools: StrictBool = False


class QueryCitation(BaseModel):
    """A document citation, built only from trusted chunk metadata."""

    id: str
    source_type: Literal["document"]
    document_id: UUID
    chunk_id: UUID
    filename: str
    page: int | None
    excerpt: str


class QueryResponse(BaseModel):
    """The ``200`` body of ``POST /v1/query``, answered or insufficient."""

    answer: str
    status: Literal["answered", "insufficient_context"]
    citations: list[QueryCitation]
    tools_used: list[str]
