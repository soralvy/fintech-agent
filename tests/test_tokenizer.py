"""The tiktoken adapter, exercised through an injected loader.

No test here loads real encoding data: ``conftest`` replaces
``tiktoken.get_encoding`` for every test, and these tests either inject a fake
encoding or prove the adapter fails safely when the loader is unavailable.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence

import pytest

from app.errors import TokenizerUnavailableError
from app.tokenizer import EMBEDDING_ENCODING, TiktokenTokenizer


class FakeEncoding:
    """Byte-level stand-in: one token per UTF-8 byte, like a byte-level BPE."""

    def __init__(self) -> None:
        self.ordinary_calls = 0

    def encode_ordinary(self, text: str) -> list[int]:
        self.ordinary_calls += 1
        return list(text.encode("utf-8"))

    def decode_bytes(self, tokens: Sequence[int]) -> bytes:
        return bytes(tokens)


def test_encoding_is_loaded_lazily_and_once() -> None:
    loaded: list[str] = []
    encoding = FakeEncoding()

    def loader(name: str) -> FakeEncoding:
        loaded.append(name)
        return encoding

    tokenizer = TiktokenTokenizer(loader=loader)
    assert loaded == [], "constructing the adapter must not load encoding data"

    tokenizer.encode("revenue")
    tokenizer.encode("<|endoftext|> is plain text in an upload")

    assert loaded == [EMBEDDING_ENCODING] == ["cl100k_base"]
    assert encoding.ordinary_calls == 2


def test_round_trip_through_the_adapter() -> None:
    tokenizer = TiktokenTokenizer(loader=lambda name: FakeEncoding())

    assert tokenizer.decode(tokenizer.encode("Umsatz — Europa")) == "Umsatz — Europa"


def test_partial_characters_at_window_edges_are_dropped_not_replaced() -> None:
    tokenizer = TiktokenTokenizer(loader=lambda name: FakeEncoding())
    tokens = tokenizer.encode("€uro €")  # "€" is three UTF-8 bytes

    assert tokenizer.decode(tokens[1:-1]) == "uro "


def test_load_failure_is_safe_and_retried(caplog: pytest.LogCaptureFixture) -> None:
    attempts = 0
    secret_detail = "/Users/someone/.cache/tiktoken/9b5ad71b2ce5302211f9c61530b329a4"

    def failing_loader(name: str) -> FakeEncoding:
        nonlocal attempts
        attempts += 1
        raise OSError(f"download failed; body=<html>denied</html> {secret_detail}")

    caplog.set_level(logging.DEBUG)
    tokenizer = TiktokenTokenizer(loader=failing_loader)

    with pytest.raises(TokenizerUnavailableError) as raised:
        tokenizer.encode("text")
    with pytest.raises(TokenizerUnavailableError):
        tokenizer.encode("text")

    assert attempts == 2, "a failed load must not be cached"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    assert secret_detail not in raised.value.message
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events[0] == {
        "event": "tokenizer.load_failed",
        "encoding": "cl100k_base",
        "error_type": "OSError",
    }
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_detail not in logged and "denied" not in logged


def test_default_loader_goes_through_tiktoken_and_fails_safely_offline() -> None:
    """The production path calls ``tiktoken.get_encoding``; in tests that is
    replaced by a refusing stub, so this proves the wiring without a download."""
    with pytest.raises(TokenizerUnavailableError):
        TiktokenTokenizer().encode("text")


# ---------------------------------------------------------------------------
# ``ensure_ready``: the bounded, off-event-loop load
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ensure_ready_loads_once_and_encode_needs_no_further_load() -> None:
    loaded: list[str] = []
    encoding = FakeEncoding()

    def loader(name: str) -> FakeEncoding:
        loaded.append(name)
        return encoding

    tokenizer = TiktokenTokenizer(loader=loader)

    await tokenizer.ensure_ready()
    await tokenizer.ensure_ready()

    assert loaded == ["cl100k_base"]
    assert tokenizer.encode("hi") == list(b"hi")
    assert loaded == ["cl100k_base"], "encode after ensure_ready must not reload"


@pytest.mark.anyio
async def test_ensure_ready_times_out_instead_of_waiting_for_a_stalled_loader(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """tiktoken's own loader has no HTTP timeout; a stalled load must not hang
    whatever called ``ensure_ready``, even though the worker thread itself may
    still be running when the deadline fires."""

    def slow_loader(name: str) -> FakeEncoding:
        time.sleep(0.2)
        return FakeEncoding()

    caplog.set_level(logging.DEBUG)
    tokenizer = TiktokenTokenizer(loader=slow_loader, timeout_seconds=0.02)
    started = time.monotonic()

    with pytest.raises(TokenizerUnavailableError) as raised:
        await tokenizer.ensure_ready()

    assert time.monotonic() - started < 0.2, (
        "must return by the deadline, not the load time"
    )
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events == [
        {
            "event": "tokenizer.load_failed",
            "encoding": "cl100k_base",
            "error_type": "TimeoutError",
        }
    ]


@pytest.mark.anyio
async def test_ensure_ready_propagates_load_errors_safely() -> None:
    secret_detail = "/Users/someone/.cache/tiktoken/9b5ad71b2ce5302211f9c61530b329a4"

    def failing_loader(name: str) -> FakeEncoding:
        raise OSError(f"download failed; body=<html>denied</html> {secret_detail}")

    tokenizer = TiktokenTokenizer(loader=failing_loader)

    with pytest.raises(TokenizerUnavailableError) as raised:
        await tokenizer.ensure_ready()

    assert secret_detail not in raised.value.message
