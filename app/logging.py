"""Structured, secret-safe application events (docs/DECISIONS.md section 19).

Each event is one log record whose message is a single JSON object: the event
name plus a small set of scalar fields. Callers pass only the safe fields that
DECISIONS section 19 lists -- never document text, vectors, full checksums,
exception messages, provider bodies, or secrets. Fields are restricted to
scalars so a list such as an embedding cannot be logged by accident.
"""

from __future__ import annotations

import json
import logging
from typing import TextIO

type EventField = str | int | float | bool | None

APP_LOGGER_NAME = "app"


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: EventField,
) -> None:
    """Emit ``event`` with ``fields`` as one JSON-compatible record."""
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
