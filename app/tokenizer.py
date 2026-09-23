"""Tokenizer boundary for chunk-size measurement (docs/DECISIONS.md section 7.5).

Chunking depends on the ``Tokenizer`` protocol, never on tiktoken directly, so
tests inject a deterministic fake and never touch tiktoken's encoding data.

The production adapter loads ``cl100k_base`` -- the encoding of
``text-embedding-3-small`` -- lazily, on the first ingestion rather than at
startup. If the encoding file is not already in tiktoken's cache, tiktoken
downloads it once and verifies it against a pinned SHA-256 before caching it
(``TIKTOKEN_CACHE_DIR`` overrides the cache location). Any failure to load it
becomes ``TokenizerUnavailableError``; the next ingestion tries again.

tiktoken's own loader has no HTTP timeout, so a stalled download would block
whatever called it indefinitely. ``ensure_ready`` runs that load in a worker
thread under a fixed deadline (``asyncio.wait_for`` + ``asyncio.to_thread``),
so a caller on the event loop -- ``Ingestor`` calls it before chunking -- is
bounded even though the background thread may still be blocked when the
deadline fires (docs/DECISIONS.md section 7.5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from typing import NoReturn, Protocol

import tiktoken

from app.errors import TokenizerUnavailableError
from app.logging import log_event

logger = logging.getLogger(__name__)

EMBEDDING_ENCODING = "cl100k_base"

# Bounds the worker thread that loads (and may download) the encoding. Chosen
# well above a warm in-cache load and comfortably below a request timeout a
# caller might apply, without waiting indefinitely on a stalled download.
DEFAULT_LOAD_TIMEOUT_SECONDS = 10.0


class Tokenizer(Protocol):
    """Deterministic text <-> token conversion."""

    async def ensure_ready(self) -> None:
        """Load anything the tokenizer needs, under a bounded deadline.

        Callers on the event loop must await this before ``encode``/``decode``
        can reach code that does blocking I/O.
        """
        ...

    def encode(self, text: str) -> list[int]: ...

    def decode(self, tokens: Sequence[int]) -> str: ...


class _Encoding(Protocol):
    """The subset of ``tiktoken.Encoding`` the adapter uses."""

    def encode_ordinary(self, text: str) -> list[int]: ...

    def decode_bytes(self, tokens: Sequence[int]) -> bytes: ...


def _load_with_tiktoken(name: str) -> _Encoding:
    # Looked up at call time, not bound at import, so a test that replaces
    # ``tiktoken.get_encoding`` also governs this adapter.
    return tiktoken.get_encoding(name)


class TiktokenTokenizer:
    """``Tokenizer`` backed by tiktoken, loading its encoding on first use."""

    def __init__(
        self,
        encoding_name: str = EMBEDDING_ENCODING,
        loader: Callable[[str], _Encoding] = _load_with_tiktoken,
        timeout_seconds: float = DEFAULT_LOAD_TIMEOUT_SECONDS,
    ) -> None:
        self._encoding_name = encoding_name
        self._loader = loader
        self._timeout_seconds = timeout_seconds
        self._encoding: _Encoding | None = None

    async def ensure_ready(self) -> None:
        """Load the encoding in a worker thread, bounded by ``timeout_seconds``.

        A no-op once the encoding is loaded. Call this before any request path
        that reaches ``encode``/``decode``, so a stalled download cannot block
        the event loop; the worker thread itself may still be running when the
        deadline fires, since Python threads cannot be cancelled.
        """
        if self._encoding is not None:
            return
        try:
            self._encoding = await asyncio.wait_for(
                asyncio.to_thread(self._loader, self._encoding_name),
                timeout=self._timeout_seconds,
            )
        except TimeoutError as exc:
            self._load_failed(exc)
        except (OSError, ValueError, ImportError) as exc:
            self._load_failed(exc)

    def _get_encoding(self) -> _Encoding:
        if self._encoding is None:
            try:
                self._encoding = self._loader(self._encoding_name)
            except (OSError, ValueError, ImportError) as exc:
                self._load_failed(exc)
        return self._encoding

    def _load_failed(self, exc: Exception) -> NoReturn:
        # tiktoken fails with a network error (``requests`` exceptions are
        # ``OSError``s), a filesystem error, a hash-mismatch or
        # unknown-encoding ``ValueError``, an ``ImportError``, or -- only from
        # ``ensure_ready`` -- a bounded-deadline ``TimeoutError``. The message
        # may carry a cache path or response body, so only the exception type
        # is recorded.
        log_event(
            logger,
            "tokenizer.load_failed",
            level=logging.ERROR,
            encoding=self._encoding_name,
            error_type=type(exc).__name__,
        )
        raise TokenizerUnavailableError from None

    def encode(self, text: str) -> list[int]:
        # ``encode_ordinary`` treats special-token text such as
        # ``<|endoftext|>`` in an upload as plain text instead of raising.
        return self._get_encoding().encode_ordinary(text)

    def decode(self, tokens: Sequence[int]) -> str:
        # A window edge can fall inside a multi-byte character. Dropping that
        # partial character, rather than inserting U+FFFD, keeps chunk text a
        # verbatim substring of the source; the overlap carries it whole.
        return (
            self._get_encoding().decode_bytes(tokens).decode("utf-8", errors="ignore")
        )
