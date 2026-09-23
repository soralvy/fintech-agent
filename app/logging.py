"""Structured, secret-safe application events (docs/DECISIONS.md section 19).

Each event is one log record whose message is a single JSON object: the event
name plus a small set of scalar fields. Callers pass only the safe fields that
DECISIONS section 19 lists -- never document text, vectors, full checksums,
exception messages, provider bodies, or secrets. Fields are restricted to
scalars so a list such as an embedding cannot be logged by accident.

The request ID travels in a ``ContextVar`` rather than through every
signature, so events from adapters deep in a call (``embedding.*``,
``retrieval.*``) still carry it. ``bind_request_id`` scopes it to one block and
always restores the previous value, so it cannot leak into a later request or
a concurrently running task.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TextIO

type EventField = str | int | float | bool | None

APP_LOGGER_NAME = "app"

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


@contextmanager
def bind_request_id(request_id: str) -> Iterator[None]:
    """Attach ``request_id`` to every event logged inside the block.

    The previous value is restored on exit, including when the block raises.
    Each asyncio task runs in a copy of the context, so concurrent requests
    never see each other's ID.
    """
    token = _request_id.set(request_id)
    try:
        yield
    finally:
        _request_id.reset(token)


def current_request_id() -> str | None:
    """The request ID bound by the innermost ``bind_request_id``, if any."""
    return _request_id.get()


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: EventField,
) -> None:
    """Emit ``event`` with ``fields`` as one JSON-compatible record.

    A bound request ID is added unless the caller passed ``request_id``
    explicitly.
    """
    request_id = _request_id.get()
    if request_id is not None and "request_id" not in fields:
        fields["request_id"] = request_id
    payload: dict[str, EventField] = {"event": event, **fields}
    logger.log(
        level,
        json.dumps(payload, sort_keys=True, ensure_ascii=False),
        extra={"event": event, "event_fields": dict(fields)},
    )


class _EventHandler(logging.StreamHandler[TextIO]):
    """Marker type so ``configure_logging`` attaches its handler only once."""


def configure_logging() -> None:
    """Make ``app.*`` INFO events visible when run under uvicorn.

    Idempotent: the handler is attached once. The record message is already
    JSON, so the formatter adds nothing to it.
    """
    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, _EventHandler) for h in logger.handlers):
        handler = _EventHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
