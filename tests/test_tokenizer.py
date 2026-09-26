"""The tiktoken adapter, exercised through an injected loader.

No test here loads real encoding data: ``conftest`` replaces
``tiktoken.get_encoding`` for every test, and these tests either inject a fake
encoding or prove the adapter fails safely when the loader is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Sequence

import pytest
import tiktoken

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


# ---------------------------------------------------------------------------
# Single flight and the timeout latch (docs/DECISIONS.md section 7.5)
#
# No wall-clock sleep decides an outcome: ``asyncio.sleep(0)`` only yields,
# and a gated loader stays blocked until the test releases its gate, so a
# short deadline always expires first. Every gate is released in ``finally``,
# so a failed assertion cannot leave a worker thread blocked.
# ---------------------------------------------------------------------------


class GatedLoader:
    """A loader that blocks its worker thread until ``gate`` is set."""

    def __init__(self, *failures: Exception) -> None:
        self.gate = threading.Event()
        self.calls = 0
        self._failures = list(failures)

    def __call__(self, name: str) -> FakeEncoding:
        self.calls += 1
        self.gate.wait()
        if self._failures:
            raise self._failures.pop(0)
        return FakeEncoding()


def _tokenizer_records(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(r.getMessage()) for r in caplog.records if r.name == "app.tokenizer"
    ]


def _load_failed(error_type: str) -> dict[str, object]:
    return {
        "event": "tokenizer.load_failed",
        "encoding": "cl100k_base",
        "error_type": error_type,
    }


TIMEOUT_EVENT = _load_failed("TimeoutError")


@pytest.mark.anyio
async def test_concurrent_first_calls_share_one_load() -> None:
    loader = GatedLoader()
    tokenizer = TiktokenTokenizer(loader=loader)
    try:
        waiters = [asyncio.create_task(tokenizer.ensure_ready()) for _ in range(5)]
        await asyncio.sleep(0)
        loader.gate.set()
        await asyncio.gather(*waiters)
    finally:
        loader.gate.set()

    assert loader.calls == 1
    assert tokenizer.encode("hi") == list(b"hi")
    assert loader.calls == 1


@pytest.mark.anyio
async def test_joining_a_finished_but_unsettled_load_stores_the_encoding() -> None:
    loaded: list[str] = []

    def loader(name: str) -> FakeEncoding:
        loaded.append(name)
        return FakeEncoding()

    async def finished_load() -> FakeEncoding:
        return FakeEncoding()

    tokenizer = TiktokenTokenizer(loader=loader)
    # A load task that is done but whose ``_load_settled`` callback has not
    # run yet: the state a caller can join between the two event-loop steps.
    task = asyncio.create_task(finished_load())
    await asyncio.wait({task})
    tokenizer._load_task = task

    await tokenizer.ensure_ready()

    assert tokenizer._encoding is not None
    assert tokenizer.encode("hi") == list(b"hi")
    assert loaded == [], "encode must not reach the inline load"


@pytest.mark.anyio
async def test_a_completed_load_failure_is_retried_by_the_next_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    loader = GatedLoader(OSError("download failed"))
    tokenizer = TiktokenTokenizer(loader=loader)
    try:
        waiters = [asyncio.create_task(tokenizer.ensure_ready()) for _ in range(3)]
        await asyncio.sleep(0)
        loader.gate.set()
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
    finally:
        loader.gate.set()

    assert all(isinstance(o, TokenizerUnavailableError) for o in outcomes)
    assert all(
        o.__cause__ is None and o.__suppress_context__
        for o in outcomes
        if isinstance(o, BaseException)
    )
    assert tokenizer._unavailable is False
    assert tokenizer._load_task is None
    # Each waiter on the one failed load logs once, as before single flight.
    assert _tokenizer_records(caplog) == [_load_failed("OSError")] * 3

    await tokenizer.ensure_ready()

    assert loader.calls == 2
    assert tokenizer.encode("hi") == list(b"hi")


@pytest.mark.anyio
async def test_a_timed_out_load_latches_and_later_calls_fail_fast(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    gate = threading.Event()
    calls = 0

    def gated_get_encoding(name: str) -> FakeEncoding:
        nonlocal calls
        calls += 1
        gate.wait()
        return FakeEncoding()

    monkeypatch.setattr(tiktoken, "get_encoding", gated_get_encoding)
    tokenizer = TiktokenTokenizer(timeout_seconds=0.01)
    try:
        with pytest.raises(TokenizerUnavailableError) as first:
            await tokenizer.ensure_ready()
        assert first.value.__cause__ is None and first.value.__suppress_context__
        assert _tokenizer_records(caplog) == [TIMEOUT_EVENT]

        task = tokenizer._load_task
        assert task is not None and not task.done()

        for _ in range(2):
            with pytest.raises(TokenizerUnavailableError) as later:
                await tokenizer.ensure_ready()
            assert later.value.__cause__ is None and later.value.__suppress_context__
        with pytest.raises(TokenizerUnavailableError) as sync:
            tokenizer.encode("text")
        assert sync.value.__cause__ is None and sync.value.__suppress_context__

        assert calls == 1
        assert _tokenizer_records(caplog) == [TIMEOUT_EVENT]
    finally:
        gate.set()

    await asyncio.wait({task})

    assert tokenizer._encoding is None, "a success after the latch is discarded"
    with pytest.raises(TokenizerUnavailableError):
        await tokenizer.ensure_ready()
    assert calls == 1
    assert _tokenizer_records(caplog) == [TIMEOUT_EVENT]


@pytest.mark.anyio
async def test_waiters_on_a_timed_out_load_log_the_transition_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    loader = GatedLoader()
    tokenizer = TiktokenTokenizer(loader=loader, timeout_seconds=0.01)
    try:
        waiters = [asyncio.create_task(tokenizer.ensure_ready()) for _ in range(2)]
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        task = tokenizer._load_task
    finally:
        loader.gate.set()

    assert all(isinstance(o, TokenizerUnavailableError) for o in outcomes)
    assert _tokenizer_records(caplog) == [TIMEOUT_EVENT]
    assert loader.calls == 1
    assert task is not None
    await asyncio.wait({task})


@pytest.mark.anyio
async def test_a_fresh_instance_can_load_after_another_instance_latched() -> None:
    loader = GatedLoader()
    latched = TiktokenTokenizer(loader=loader, timeout_seconds=0.01)
    try:
        with pytest.raises(TokenizerUnavailableError):
            await latched.ensure_ready()
        task = latched._load_task
    finally:
        loader.gate.set()
    assert task is not None
    await asyncio.wait({task})

    fresh = TiktokenTokenizer(loader=lambda name: FakeEncoding())
    await fresh.ensure_ready()

    assert fresh.encode("hi") == list(b"hi")
    with pytest.raises(TokenizerUnavailableError):
        await latched.ensure_ready()


@pytest.mark.anyio
async def test_cancellation_neither_cancels_the_load_nor_latches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    # (a) A cancelled waiter leaves the shared load running.
    loader = GatedLoader()
    tokenizer = TiktokenTokenizer(loader=loader)
    try:
        waiter = asyncio.create_task(tokenizer.ensure_ready())
        await asyncio.sleep(0)
        shared = tokenizer._load_task
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert shared is not None and not shared.cancelled()
        assert tokenizer._unavailable is False

        second = asyncio.create_task(tokenizer.ensure_ready())
        await asyncio.sleep(0)
        loader.gate.set()
        await second
    finally:
        loader.gate.set()

    assert loader.calls == 1
    assert tokenizer.encode("hi") == list(b"hi")

    # (b) A cancelled shared load (event-loop shutdown only) propagates.
    loader = GatedLoader()
    tokenizer = TiktokenTokenizer(loader=loader)
    try:
        waiter = asyncio.create_task(tokenizer.ensure_ready())
        await asyncio.sleep(0)
        shared = tokenizer._load_task
        assert shared is not None
        shared.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    finally:
        loader.gate.set()

    assert shared.cancelled()
    assert tokenizer._unavailable is False
    assert tokenizer._load_task is None
    assert _tokenizer_records(caplog) == []
    assert not [r for r in caplog.records if r.name == "asyncio"]
