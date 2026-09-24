"""OpenAI adapter behind an application-facing embedding interface.

Ingestion depends on ``Embedder``, never on the SDK, so tests use a
deterministic fake and make no network call (docs/DECISIONS.md section 3.3).
The adapter returns plain float lists and never leaks raw SDK responses or
errors: a provider failure surfaces as ``EmbeddingProviderError``, whose public
message is fixed, and only the failure's type and status code are logged.

The answering graph depends on ``AnswerGenerator`` the same way and receives
an untrusted ``GroundedAnswer``, whose citation labels the application still
validates (docs/DECISIONS.md section 10.9). ``OpenAIAnswerGenerator`` makes each
logical call as one ``responses.create`` with the application-owned strict
schema, then classifies the result itself: status before any parsing, refusal
in any message, then exactly one usable output text, parsed with ``json.loads``
and validated by ``GroundedAnswer``. Only invalid structured output is retried,
exactly once (docs/DECISIONS.md section 12). Every failure surfaces as
``AnswerProviderError``; refusal text, output, prompts, and provider bodies are
never logged.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal, NoReturn, Protocol

import openai
from openai import AsyncOpenAI
from openai.types.responses import (
    Response,
    ResponseFormatTextJSONSchemaConfigParam,
    ResponseOutputMessage,
    ResponseOutputRefusal,
    ResponseOutputText,
    ResponseUsage,
)
from openai.types.responses.response import IncompleteDetails
from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import OpenAIConfig
from app.errors import AnswerProviderError, EmbeddingProviderError
from app.logging import log_event

logger = logging.getLogger(__name__)

# Inputs per embeddings request. At ~800 tokens per chunk this stays well
# inside the API's per-request input and token limits.
EMBEDDING_BATCH_SIZE = 128


class Embedder(Protocol):
    """Turns texts into vectors, one per input, in input order."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class GroundedAnswer(BaseModel):
    """The answer model's structured output: untrusted until ``finalize``.

    Strict and closed, so a string ``"true"`` or an extra field is rejected
    rather than coerced (docs/TECH_BASELINE.md section 3.10).
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str
    citation_ids: list[str]
    insufficient_context: bool


# The provider-side schema for ``GroundedAnswer``: the same three required
# fields and types, closed with ``additionalProperties: false``. It is written
# out by hand rather than derived, so what is sent to the provider is exactly
# this constant; a test pins it against the Pydantic model.
GROUNDED_ANSWER_FORMAT: Final[ResponseFormatTextJSONSchemaConfigParam] = {
    "type": "json_schema",
    "name": "grounded_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "citation_ids": {"type": "array", "items": {"type": "string"}},
            "insufficient_context": {"type": "boolean"},
        },
        "required": ["answer", "citation_ids", "insufficient_context"],
        "additionalProperties": False,
    },
}

# Code constants, not configuration (docs/TECH_BASELINE.md section 3.10). The
# output budget covers the visible JSON answer plus any reasoning tokens.
ANSWER_REASONING_EFFORT: Final = "none"
ANSWER_MAX_OUTPUT_TOKENS: Final = 1200

# Logical calls per answer: the first, plus one retry for invalid structured
# output only. SDK transport retries happen inside each logical call.
_MAX_LOGICAL_CALLS = 2

# Internal reasons, logged by ``generation.invalid_output``. Only the last four
# (invalid structured output) are retried.
type _RejectReason = Literal[
    "malformed_response",
    "incomplete_max_output_tokens",
    "incomplete_content_filter",
    "incomplete_other",
    "unexpected_status",
    "refusal",
    "no_output_text",
    "multiple_output_text",
    "invalid_json",
    "schema_validation",
]


@dataclass(frozen=True, slots=True)
class _Rejected:
    reason: _RejectReason
    retryable: bool


class AnswerGenerator(Protocol):
    """Produces one grounded answer from fixed instructions and a rendered prompt.

    The graph renders the prompt; implementations receive only the two
    strings (docs/DECISIONS.md section 4).
    """

    async def generate_answer(
        self, *, instructions: str, prompt: str
    ) -> GroundedAnswer: ...


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


class OpenAIAnswerGenerator:
    """``AnswerGenerator`` backed by the OpenAI Responses API."""

    def __init__(self, client: AsyncOpenAI, *, model: str) -> None:
        self._client = client
        self._model = model

    async def generate_answer(
        self, *, instructions: str, prompt: str
    ) -> GroundedAnswer:
        """Return one validated answer, or raise ``AnswerProviderError``.

        At most two logical calls, both with identical inputs: the second only
        after the first returned invalid structured output. Any other failure,
        on either call, is raised at once.
        """
        for attempt in range(1, _MAX_LOGICAL_CALLS + 1):
            started = time.monotonic()
            response = await self._create(attempt, instructions, prompt)
            outcome = _classify(response)
            if isinstance(outcome, GroundedAnswer):
                log_event(
                    logger,
                    "generation.completed",
                    attempt=attempt,
                    input_tokens=_token_count(response, "input_tokens"),
                    output_tokens=_token_count(response, "output_tokens"),
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                return outcome
            will_retry = outcome.retryable and attempt < _MAX_LOGICAL_CALLS
            log_event(
                logger,
                "generation.invalid_output",
                level=logging.WARNING if will_retry else logging.ERROR,
                attempt=attempt,
                reason=outcome.reason,
                will_retry=will_retry,
            )
            if not will_retry:
                break
        raise AnswerProviderError

    async def _create(self, attempt: int, instructions: str, prompt: str) -> Response:
        try:
            return await self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=prompt,
                text={"format": GROUNDED_ANSWER_FORMAT},
                reasoning={"effort": ANSWER_REASONING_EFFORT},
                max_output_tokens=ANSWER_MAX_OUTPUT_TOKENS,
                store=False,
            )
        except openai.OpenAIError as exc:
            # The SDK error may quote the response body; only its type and
            # status code are safe to record.
            log_event(
                logger,
                "generation.request_failed",
                level=logging.ERROR,
                attempt=attempt,
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
            )
            raise AnswerProviderError from None
        except (ValueError, RecursionError):
            # A 200 labelled JSON whose body the SDK cannot decode: invalid
            # JSON, invalid UTF-8, or nesting too deep. The error carries the
            # body, so nothing of it is logged. The arguments above are fixed
            # constants, so no programming error is hidden here.
            log_event(
                logger,
                "generation.invalid_output",
                level=logging.ERROR,
                attempt=attempt,
                reason="malformed_response",
                will_retry=False,
            )
            raise AnswerProviderError from None


def _classify(response: Response) -> GroundedAnswer | _Rejected:
    """Classify one logical call's response (docs/DECISIONS.md section 12).

    In table order: a malformed body; the status, checked before any output
    is read; a refusal
    in any message, which wins over any payload; then exactly one usable
    output text. Every output item is inspected: the answer is never assumed
    to be ``output[0]``.
    """
    if not _is_well_formed(response):
        return _Rejected("malformed_response", retryable=False)
    rejected = _status_rejection(response)
    if rejected is not None:
        return rejected
    messages = [
        item for item in response.output if isinstance(item, ResponseOutputMessage)
    ]
    if any(
        isinstance(part, ResponseOutputRefusal)
        for message in messages
        for part in message.content
    ):
        return _Rejected("refusal", retryable=False)
    payloads = [
        part.text
        for message in messages
        if message.phase in (None, "final_answer")
        for part in message.content
        if isinstance(part, ResponseOutputText)
    ]
    if len(payloads) != 1:
        reason: _RejectReason = "multiple_output_text" if payloads else "no_output_text"
        return _Rejected(reason, retryable=True)
    return _parse(payloads[0])


def _is_well_formed(response: object) -> bool:
    """Whether every field the classification reads has its declared type.

    The SDK does not validate response bodies by default: a non-JSON 200
    comes back as a plain ``str``, and a JSON body of the wrong shape as an
    unvalidated ``Response`` (docs/DECISIONS.md section 12).
    """
    if not isinstance(response, Response) or not isinstance(response.output, list):
        return False
    details = response.incomplete_details
    if details is not None and not isinstance(details, IncompleteDetails):
        return False
    for item in response.output:
        if not isinstance(item, ResponseOutputMessage):
            continue
        if not isinstance(item.content, list):
            return False
        for part in item.content:
            if isinstance(part, ResponseOutputText) and not isinstance(part.text, str):
                return False
    return True


def _token_count(
    response: Response, name: Literal["input_tokens", "output_tokens"]
) -> int | None:
    """A usage count safe to log: an ``int``, or ``None`` for anything else.

    Usage is only logged, never classified, so a malformed ``usage`` does not
    reject an otherwise valid answer; it is simply not recorded.
    """
    usage = response.usage
    if not isinstance(usage, ResponseUsage):
        return None
    count = getattr(usage, name, None)
    if isinstance(count, bool) or not isinstance(count, int):
        return None
    return count


def _status_rejection(response: Response) -> _Rejected | None:
    if response.status == "incomplete":
        details = response.incomplete_details
        match details.reason if details is not None else None:
            case "max_output_tokens":
                return _Rejected("incomplete_max_output_tokens", retryable=False)
            case "content_filter":
                return _Rejected("incomplete_content_filter", retryable=False)
            case _:
                return _Rejected("incomplete_other", retryable=False)
    if response.status != "completed":
        return _Rejected("unexpected_status", retryable=False)
    return None


def _parse(payload: str) -> GroundedAnswer | _Rejected:
    try:
        data = json.loads(payload)
    except (ValueError, RecursionError):
        return _Rejected("invalid_json", retryable=True)
    try:
        return GroundedAnswer.model_validate(data)
    except ValidationError:
        return _Rejected("schema_validation", retryable=True)
