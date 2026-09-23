"""Request-ID binding: scoped, restored on every exit, and isolated per task."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from app.logging import bind_request_id, current_request_id, log_event

logger = logging.getLogger("app.test_logging")


def _logged(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == logger.name
    ]


def test_bound_request_id_is_added_to_events(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    with bind_request_id("req-1"):
        log_event(logger, "demo.inside")
    log_event(logger, "demo.after")

    inside, after = _logged(caplog)
    assert inside["request_id"] == "req-1"
    assert "request_id" not in after


def test_explicit_request_id_wins_over_the_bound_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    with bind_request_id("bound"):
        log_event(logger, "demo.explicit", request_id="explicit")

    (event,) = _logged(caplog)
    assert event["request_id"] == "explicit"


def test_request_id_does_not_leak_after_an_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    with pytest.raises(RuntimeError), bind_request_id("req-failing"):
        raise RuntimeError("boom")
    log_event(logger, "demo.next_operation")

    assert current_request_id() is None
    (event,) = _logged(caplog)
    assert "request_id" not in event


def test_nested_binding_restores_the_outer_id() -> None:
    with bind_request_id("outer"):
        with bind_request_id("inner"):
            assert current_request_id() == "inner"
        assert current_request_id() == "outer"
    assert current_request_id() is None


@pytest.mark.anyio
async def test_concurrent_tasks_keep_their_own_request_id() -> None:
    both_bound = asyncio.Barrier(2)

    async def run(request_id: str) -> str | None:
        with bind_request_id(request_id):
            await both_bound.wait()
            return current_request_id()

    assert list(await asyncio.gather(run("a"), run("b"))) == ["a", "b"]
    assert current_request_id() is None
