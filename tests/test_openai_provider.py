"""The OpenAI adapters against the real SDK over a mock transport.

The SDK parses real HTTP responses, but every request is answered in-process
by ``httpx2.MockTransport``; nothing reaches the network and the key is fake.
Answer-adapter tests set ``max_retries=0``, so each HTTP request is exactly one
logical model call (docs/DECISIONS.md section 12); one test enables a single
transport retry to show the two budgets stay separate.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, get_args

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.errors import AnswerProviderError, AppError, EmbeddingProviderError
from app.mcp_client import ALLOWED_TOOLS, MarketToolName
from app.openai_provider import (
    ANSWER_MAX_OUTPUT_TOKENS,
    ANSWER_REASONING_EFFORT,
    GROUNDED_ANSWER_FORMAT,
    PLANNER_MAX_OUTPUT_TOKENS,
    PLANNER_REASONING_EFFORT,
    PLANNER_TIMEOUT_SECONDS,
    TOOL_PLAN_FORMAT,
    GroundedAnswer,
    OpenAIAnswerGenerator,
    OpenAIEmbedder,
    OpenAIToolPlanner,
    PlannedToolName,
    ToolPlan,
    ToolPlanningError,
)

pytestmark = pytest.mark.anyio

FAKE_KEY = "sk-test-not-a-real-key"
DIMENSIONS = 1536

Handler = Callable[[httpx2.Request], httpx2.Response]


def make_embedder(handler: Handler, batch_size: int = 128) -> OpenAIEmbedder:
    client = AsyncOpenAI(
        api_key=FAKE_KEY,
        base_url="http://openai.invalid/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    return OpenAIEmbedder(
        client,
        model="text-embedding-3-small",
        dimensions=DIMENSIONS,
        batch_size=batch_size,
    )


def embeddings_response(
    inputs: list[str], *, dimensions: int = DIMENSIONS, reverse: bool = False
) -> httpx2.Response:
    """A well-formed response whose vector ``i`` starts with ``float(i)``."""
    indexes = list(range(len(inputs)))
    if reverse:
        indexes.reverse()
    data = [
        {
            "object": "embedding",
            "index": i,
            "embedding": [float(i)] + [0.0] * (dimensions - 1),
        }
        for i in indexes
    ]
    return httpx2.Response(
        200,
        json={
            "object": "list",
            "data": data,
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        },
    )


async def test_request_carries_model_dimensions_and_batch() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        return embeddings_response(body["input"])

    vectors = await make_embedder(handler).embed(["a", "b"])

    assert requests == [
        {
            "input": ["a", "b"],
            "model": "text-embedding-3-small",
            "dimensions": 1536,
            "encoding_format": "float",
        }
    ]
    assert [len(v) for v in vectors] == [1536, 1536]


async def test_inputs_are_batched_and_order_restored_by_index() -> None:
    batches: list[list[str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        inputs = json.loads(request.content)["input"]
        batches.append(inputs)
        return embeddings_response(inputs, reverse=True)

    vectors = await make_embedder(handler, batch_size=2).embed(list("abcde"))

    assert batches == [["a", "b"], ["c", "d"], ["e"]]
    assert [v[0] for v in vectors] == [0.0, 1.0, 0.0, 1.0, 0.0]


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_provider_error_is_safe(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    body = f"upstream failure echoing Authorization: Bearer {FAKE_KEY}"

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": body}})

    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmbeddingProviderError) as raised:
        await make_embedder(handler).embed(["a"])

    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    event = json.loads(caplog.records[-1].getMessage())
    assert event["event"] == "embedding.request_failed"
    assert event["status_code"] == status
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert FAKE_KEY not in logged and "upstream failure" not in logged


async def test_connection_failure_is_safe() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"cannot reach host with key {FAKE_KEY}")

    with pytest.raises(EmbeddingProviderError) as raised:
        await make_embedder(handler).embed(["a"])

    assert FAKE_KEY not in str(raised.value)


async def test_wrong_dimension_response_is_rejected() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return embeddings_response(json.loads(request.content)["input"], dimensions=8)

    with pytest.raises(EmbeddingProviderError):
        await make_embedder(handler).embed(["a"])


async def test_missing_vector_is_rejected() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return embeddings_response(json.loads(request.content)["input"][:-1])

    with pytest.raises(EmbeddingProviderError):
        await make_embedder(handler).embed(["a", "b"])


# ---------------------------------------------------------------------------
# Structured grounded-answer adapter (AC8, AC10 adapter events)
# ---------------------------------------------------------------------------

MODEL = "gpt-6-luna"
INSTRUCTIONS = "sentinel-instructions-4b2: answer from the sources."
PROMPT = "<question>sentinel-question-9d1</question>\n<sources>\n</sources>"
EXPECTED = GroundedAnswer(
    answer="Revenue fell [D1].", citation_ids=["D1"], insufficient_context=False
)
VALID: dict[str, Any] = EXPECTED.model_dump()
REFUSAL_TEXT = "sentinel-refusal-text-3e8"
PAYLOAD_SENTINEL = "sentinel-payload-5a0"

Json = dict[str, Any]


def text(value: str) -> Json:
    return {"type": "output_text", "text": value, "annotations": []}


def refusal(value: str = REFUSAL_TEXT) -> Json:
    return {"type": "refusal", "refusal": value}


def message(*parts: Json, phase: str | None = None) -> Json:
    item: Json = {
        "type": "message",
        "id": "msg_1",
        "role": "assistant",
        "status": "completed",
        "content": list(parts),
    }
    if phase is not None:
        item["phase"] = phase
    return item


def reasoning() -> Json:
    return {"type": "reasoning", "id": "rs_1", "summary": []}


def answer_text(payload: object = VALID) -> Json:
    return text(json.dumps(payload))


def response(
    *output: Json,
    status: str | None = "completed",
    incomplete_reason: str | None = None,
) -> httpx2.Response:
    body: Json = {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": MODEL,
        "output": list(output),
        "usage": {
            "input_tokens": 321,
            "output_tokens": 45,
            "total_tokens": 366,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
    }
    if status is not None:
        body["status"] = status
    if incomplete_reason is not None:
        body["incomplete_details"] = {"reason": incomplete_reason}
    return httpx2.Response(200, json=body)


def valid_response() -> httpx2.Response:
    return response(message(answer_text()))


class Script:
    """Answers each request with the next queued response, recording bodies."""

    def __init__(self, *responses: httpx2.Response | Exception) -> None:
        self._responses = list(responses)
        self.bodies: list[Json] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        assert self._responses, "unexpected extra request"
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def calls(self) -> int:
        return len(self.bodies)


def make_generator(script: Script, *, max_retries: int = 0) -> OpenAIAnswerGenerator:
    client = AsyncOpenAI(
        api_key=FAKE_KEY,
        base_url="http://openai.invalid/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(script)),
        max_retries=max_retries,
    )
    return OpenAIAnswerGenerator(client, model=MODEL)


async def generate(script: Script) -> GroundedAnswer:
    return await make_generator(script).generate_answer(
        instructions=INSTRUCTIONS, prompt=PROMPT
    )


def generation_events(caplog: pytest.LogCaptureFixture) -> list[Json]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.openai_provider"
    ]


def invalid_reasons(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str, bool]]:
    return [
        (event["attempt"], event["reason"], event["will_retry"])
        for event in generation_events(caplog)
        if event["event"] == "generation.invalid_output"
    ]


def assert_nothing_sensitive_logged(caplog: pytest.LogCaptureFixture) -> None:
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        FAKE_KEY,
        INSTRUCTIONS,
        PROMPT,
        "sentinel-question-9d1",
        REFUSAL_TEXT,
        PAYLOAD_SENTINEL,
        VALID["answer"],
        "upstream failure",
    ):
        assert str(secret) not in logged


# --- Request shape -----------------------------------------------------------


async def test_the_request_carries_the_recorded_settings() -> None:
    script = Script(valid_response())

    await generate(script)

    assert script.bodies == [
        {
            "model": "gpt-6-luna",
            "instructions": INSTRUCTIONS,
            "input": PROMPT,
            "text": {"format": GROUNDED_ANSWER_FORMAT},
            "reasoning": {"effort": "none"},
            "max_output_tokens": 1200,
            "store": False,
        }
    ]
    assert (ANSWER_REASONING_EFFORT, ANSWER_MAX_OUTPUT_TOKENS) == ("none", 1200)


async def test_the_retry_sends_identical_inputs() -> None:
    script = Script(response(message(text("not json"))), valid_response())

    await generate(script)

    assert script.calls == 2
    assert script.bodies[0] == script.bodies[1]


# --- Schema contract ---------------------------------------------------------


def test_the_schema_constant_matches_the_pydantic_model() -> None:
    assert GROUNDED_ANSWER_FORMAT["type"] == "json_schema"
    assert GROUNDED_ANSWER_FORMAT["name"] == "grounded_answer"
    assert GROUNDED_ANSWER_FORMAT["strict"] is True
    schema: Any = GROUNDED_ANSWER_FORMAT["schema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    fields = list(GroundedAnswer.model_fields)
    assert schema["required"] == list(schema["properties"]) == fields

    model_schema = GroundedAnswer.model_json_schema()
    assert model_schema["required"] == fields
    assert model_schema["additionalProperties"] is False
    for name in fields:
        derived = {
            key: value
            for key, value in model_schema["properties"][name].items()
            if key != "title"
        }
        assert schema["properties"][name] == derived, name


@pytest.mark.parametrize("missing", ["answer", "citation_ids", "insufficient_context"])
def test_the_model_rejects_a_missing_field(missing: str) -> None:
    payload = {key: value for key, value in VALID.items() if key != missing}

    with pytest.raises(ValidationError):
        GroundedAnswer.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("answer", 5),
        ("answer", None),
        ("citation_ids", "D1"),
        ("citation_ids", [1]),
        ("insufficient_context", "true"),
        ("insufficient_context", 0),
    ],
)
def test_the_model_rejects_a_wrong_type(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        GroundedAnswer.model_validate({**VALID, field: value})


def test_the_model_rejects_an_extra_field() -> None:
    with pytest.raises(ValidationError):
        GroundedAnswer.model_validate({**VALID, "excerpt": "model-written"})


def test_the_model_accepts_the_exact_shape() -> None:
    assert GroundedAnswer.model_validate(VALID) == EXPECTED


# --- Outcomes and logical-call counts ------------------------------------------


async def test_valid_answer_after_a_reasoning_item_takes_one_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(response(reasoning(), message(answer_text())))

    result = await generate(script)

    assert result == EXPECTED
    assert script.calls == 1
    (completed,) = [
        e for e in generation_events(caplog) if e["event"] == "generation.completed"
    ]
    assert (completed["attempt"], completed["input_tokens"]) == (1, 321)
    assert completed["output_tokens"] == 45
    assert isinstance(completed["duration_ms"], int)
    assert_nothing_sensitive_logged(caplog)


async def test_a_final_answer_phase_payload_is_usable() -> None:
    script = Script(response(message(answer_text(), phase="final_answer")))

    assert await generate(script) == EXPECTED
    assert script.calls == 1


async def test_commentary_phase_text_is_ignored() -> None:
    script = Script(
        response(
            message(text(f"thinking {PAYLOAD_SENTINEL}"), phase="commentary"),
            message(answer_text()),
        )
    )

    assert await generate(script) == EXPECTED
    assert script.calls == 1


async def test_a_refusal_is_not_retried(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(response(message(refusal())))

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 1
    assert invalid_reasons(caplog) == [(1, "refusal", False)]
    assert_nothing_sensitive_logged(caplog)


async def test_a_refusal_in_any_message_wins_over_a_valid_payload() -> None:
    script = Script(response(message(answer_text()), message(refusal())))

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 1


@pytest.mark.parametrize(
    ("incomplete_reason", "reason"),
    [
        ("max_output_tokens", "incomplete_max_output_tokens"),
        ("content_filter", "incomplete_content_filter"),
        ("max_messages", "incomplete_other"),
        (None, "incomplete_other"),
    ],
)
async def test_an_incomplete_response_is_not_retried_or_parsed(
    caplog: pytest.LogCaptureFixture, incomplete_reason: str | None, reason: str
) -> None:
    """The truncated text would parse as valid JSON here; status comes first."""
    caplog.set_level(logging.DEBUG)
    script = Script(
        response(
            message(answer_text()),
            status="incomplete",
            incomplete_reason=incomplete_reason,
        )
    )

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 1
    assert invalid_reasons(caplog) == [(1, reason, False)]


type Factory = Callable[[], httpx2.Response]


def malformed(body: object) -> Factory:
    def build() -> httpx2.Response:
        return httpx2.Response(200, json=body)

    return build


def json_labelled(content: bytes) -> Factory:
    def build() -> httpx2.Response:
        return httpx2.Response(
            200, content=content, headers={"content-type": "application/json"}
        )

    return build


def non_json() -> httpx2.Response:
    return httpx2.Response(
        200,
        content=f"<html>{PAYLOAD_SENTINEL}</html>".encode(),
        headers={"content-type": "text/html"},
    )


_BASE: Json = {"id": "resp_1", "object": "response", "created_at": 0, "model": MODEL}
_MESSAGE: Json = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
}


@pytest.mark.parametrize(
    "build",
    [
        non_json,
        json_labelled(f"{{not json {PAYLOAD_SENTINEL}".encode()),
        json_labelled(b""),
        json_labelled(b'{"answer": "\xff"}'),
        json_labelled(b"[" * 100_000),
        malformed([1, 2]),
        malformed({**_BASE, "status": "completed"}),
        malformed({**_BASE, "status": "completed", "output": None}),
        malformed({**_BASE, "status": "completed", "output": [_MESSAGE]}),
        malformed(
            {**_BASE, "status": "incomplete", "incomplete_details": "x", "output": []}
        ),
        malformed(
            {**_BASE, "status": "incomplete", "incomplete_details": [1], "output": []}
        ),
        malformed(
            {
                **_BASE,
                "status": "completed",
                "output": [{**_MESSAGE, "content": [{**text("x"), "text": None}]}],
            }
        ),
    ],
    ids=[
        "non-json",
        "json-labelled-invalid-json",
        "json-labelled-empty",
        "json-labelled-invalid-utf8",
        "json-labelled-deep-nesting",
        "json-array",
        "missing-output",
        "null-output",
        "missing-content",
        "string-incomplete-details",
        "list-incomplete-details",
        "null-text",
    ],
)
async def test_a_malformed_success_body_is_one_call_and_a_provider_error(
    caplog: pytest.LogCaptureFixture, build: Factory
) -> None:
    """The SDK does not validate bodies by default, so a 200 of the wrong shape
    must still surface as the contracted provider error, not a crash."""
    caplog.set_level(logging.DEBUG)
    script = Script(build())

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 1
    assert invalid_reasons(caplog) == [(1, "malformed_response", False)]
    assert_nothing_sensitive_logged(caplog)


@pytest.mark.parametrize(
    "usage",
    [
        "x",
        [1],
        None,
        {"input_tokens": PAYLOAD_SENTINEL, "output_tokens": [PAYLOAD_SENTINEL]},
        {"input_tokens": True, "output_tokens": 1.5},
    ],
    ids=["string", "list", "null", "string-counts", "non-int-counts"],
)
async def test_a_malformed_usage_keeps_the_answer_and_logs_no_count(
    caplog: pytest.LogCaptureFixture, usage: object
) -> None:
    """Usage is only logged, so it never rejects a valid answer, and a
    non-integer count is never copied into the log."""
    caplog.set_level(logging.DEBUG)
    body = json.loads(valid_response().content)
    body["usage"] = usage
    script = Script(httpx2.Response(200, json=body))

    assert await generate(script) == EXPECTED

    assert script.calls == 1
    (completed,) = [
        e for e in generation_events(caplog) if e["event"] == "generation.completed"
    ]
    assert (completed["input_tokens"], completed["output_tokens"]) == (None, None)
    assert_nothing_sensitive_logged(caplog)


@pytest.mark.parametrize(
    "status", ["failed", "cancelled", "in_progress", "queued", None]
)
async def test_an_unexpected_status_is_not_retried(
    caplog: pytest.LogCaptureFixture, status: str | None
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(response(message(answer_text()), status=status))

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 1
    assert invalid_reasons(caplog) == [(1, "unexpected_status", False)]


def no_output_text() -> httpx2.Response:
    return response(reasoning())


def commentary_only() -> httpx2.Response:
    return response(message(answer_text(), phase="commentary"))


def two_texts() -> httpx2.Response:
    return response(message(answer_text(), answer_text()))


def two_messages() -> httpx2.Response:
    return response(
        message(answer_text()), message(answer_text(), phase="final_answer")
    )


def invalid_json() -> httpx2.Response:
    return response(message(text(f"{{not json {PAYLOAD_SENTINEL}")))


def extra_field() -> httpx2.Response:
    return response(message(answer_text({**VALID, "excerpt": PAYLOAD_SENTINEL})))


def string_boolean() -> httpx2.Response:
    return response(message(answer_text({**VALID, "insufficient_context": "false"})))


def refused() -> httpx2.Response:
    return response(message(refusal()))


def truncated() -> httpx2.Response:
    return response(status="incomplete", incomplete_reason="max_output_tokens")


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (no_output_text, valid_response, [(1, "no_output_text", True)]),
        (invalid_json, valid_response, [(1, "invalid_json", True)]),
        (two_texts, valid_response, [(1, "multiple_output_text", True)]),
        (string_boolean, valid_response, [(1, "schema_validation", True)]),
    ],
    ids=["no-output", "invalid-json", "multiple-output", "schema-invalid"],
)
async def test_invalid_output_then_valid_succeeds_on_the_second_call(
    caplog: pytest.LogCaptureFixture,
    first: Factory,
    second: Factory,
    expected: list[tuple[int, str, bool]],
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(first(), second())

    assert await generate(script) == EXPECTED

    assert script.calls == 2
    assert invalid_reasons(caplog) == expected
    completed = [
        e for e in generation_events(caplog) if e["event"] == "generation.completed"
    ]
    assert [e["attempt"] for e in completed] == [2]
    assert_nothing_sensitive_logged(caplog)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (
            no_output_text,
            commentary_only,
            [(1, "no_output_text", True), (2, "no_output_text", False)],
        ),
        (
            invalid_json,
            invalid_json,
            [(1, "invalid_json", True), (2, "invalid_json", False)],
        ),
        (
            two_texts,
            two_messages,
            [(1, "multiple_output_text", True), (2, "multiple_output_text", False)],
        ),
        (
            extra_field,
            extra_field,
            [(1, "schema_validation", True), (2, "schema_validation", False)],
        ),
        (
            invalid_json,
            extra_field,
            [(1, "invalid_json", True), (2, "schema_validation", False)],
        ),
        (
            extra_field,
            refused,
            [(1, "schema_validation", True), (2, "refusal", False)],
        ),
        (
            invalid_json,
            truncated,
            [(1, "invalid_json", True), (2, "incomplete_max_output_tokens", False)],
        ),
    ],
    ids=[
        "no-output-twice",
        "invalid-json-twice",
        "multiple-output-twice",
        "schema-invalid-twice",
        "second-reason-is-reported",
        "schema-invalid-then-refusal",
        "invalid-json-then-incomplete",
    ],
)
async def test_a_second_failure_raises_after_exactly_two_calls(
    caplog: pytest.LogCaptureFixture,
    first: Factory,
    second: Factory,
    expected: list[tuple[int, str, bool]],
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(first(), second())

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 2
    assert invalid_reasons(caplog) == expected
    assert_nothing_sensitive_logged(caplog)


async def test_a_provider_failure_on_the_retry_is_not_retried_again() -> None:
    script = Script(invalid_json(), httpx2.Response(500, json={"error": {}}))

    with pytest.raises(AnswerProviderError):
        await generate(script)

    assert script.calls == 2


@pytest.mark.parametrize("status", [500, 429, 401, 400])
async def test_an_http_error_is_one_call_and_safe(
    caplog: pytest.LogCaptureFixture, status: int
) -> None:
    caplog.set_level(logging.DEBUG)
    body = f"upstream failure echoing Authorization: Bearer {FAKE_KEY}"
    script = Script(httpx2.Response(status, json={"error": {"message": body}}))

    with pytest.raises(AnswerProviderError) as raised:
        await generate(script)

    assert script.calls == 1
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    (failed,) = generation_events(caplog)
    assert failed["event"] == "generation.request_failed"
    assert (failed["attempt"], failed["status_code"]) == (1, status)
    assert isinstance(failed["error_type"], str)
    assert_nothing_sensitive_logged(caplog)


async def test_a_connection_error_is_one_call_and_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(httpx2.ConnectError(f"cannot reach host with key {FAKE_KEY}"))

    with pytest.raises(AnswerProviderError) as raised:
        await generate(script)

    assert script.calls == 1
    assert FAKE_KEY not in str(raised.value)
    (failed,) = generation_events(caplog)
    assert (failed["event"], failed["status_code"]) == (
        "generation.request_failed",
        None,
    )
    assert_nothing_sensitive_logged(caplog)


async def test_sdk_transport_retries_are_separate_from_logical_calls(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One logical call may be several HTTP attempts inside the SDK."""
    caplog.set_level(logging.DEBUG)
    retry_soon = {"retry-after-ms": "1"}
    script = Script(
        httpx2.Response(500, headers=retry_soon, json={"error": {}}), valid_response()
    )
    generator = make_generator(script, max_retries=1)

    result = await generator.generate_answer(instructions=INSTRUCTIONS, prompt=PROMPT)

    assert result == EXPECTED
    assert script.calls == 2, "two HTTP attempts"
    assert [e["attempt"] for e in generation_events(caplog)] == [1], "one logical call"


# ---------------------------------------------------------------------------
# Tool planner adapter (M6 AC2, T31)
# ---------------------------------------------------------------------------

PLANNER_INSTRUCTIONS = "sentinel-planner-instructions-6c4"
PLANNER_PROMPT = "<question>sentinel-planner-question-2f7 MSFT</question>"
QUOTE_PLAN: dict[str, Any] = {"tool_name": "get_market_quote", "symbol": "msft"}
NO_PLAN: dict[str, Any] = {"tool_name": None, "symbol": None}


def make_planner(script: Script, *, max_retries: int = 2) -> OpenAIToolPlanner:
    """A planner over a shared client that, like production, allows retries."""
    client = AsyncOpenAI(
        api_key=FAKE_KEY,
        base_url="http://openai.invalid/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(script)),
        max_retries=max_retries,
    )
    return OpenAIToolPlanner(client, model=MODEL)


async def plan(script: Script) -> ToolPlan:
    return await make_planner(script).plan_tool(
        instructions=PLANNER_INSTRUCTIONS, prompt=PLANNER_PROMPT
    )


def plan_response(payload: object = QUOTE_PLAN) -> httpx2.Response:
    return response(message(answer_text(payload)))


def planning_events(caplog: pytest.LogCaptureFixture) -> list[Json]:
    return [
        event
        for event in generation_events(caplog)
        if event["event"].startswith("planning.")
    ]


def assert_no_planner_secret_logged(caplog: pytest.LogCaptureFixture) -> None:
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (
        FAKE_KEY,
        PLANNER_INSTRUCTIONS,
        PLANNER_PROMPT,
        "sentinel-planner-question-2f7",
        "msft",
        REFUSAL_TEXT,
        PAYLOAD_SENTINEL,
        "upstream failure",
    ):
        assert secret not in logged


async def test_the_planner_request_carries_the_recorded_settings() -> None:
    timeouts: list[object] = []
    script = Script(plan_response())

    def recording(request: httpx2.Request) -> httpx2.Response:
        timeouts.append(request.extensions.get("timeout"))
        return script(request)

    client = AsyncOpenAI(
        api_key=FAKE_KEY,
        base_url="http://openai.invalid/v1",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(recording)),
        max_retries=2,
    )

    await OpenAIToolPlanner(client, model=MODEL).plan_tool(
        instructions=PLANNER_INSTRUCTIONS, prompt=PLANNER_PROMPT
    )

    assert script.bodies == [
        {
            "model": "gpt-6-luna",
            "instructions": PLANNER_INSTRUCTIONS,
            "input": PLANNER_PROMPT,
            "text": {"format": TOOL_PLAN_FORMAT},
            "reasoning": {"effort": "none"},
            "max_output_tokens": 200,
            "store": False,
        }
    ]
    assert timeouts == [{"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}]
    assert (
        PLANNER_TIMEOUT_SECONDS,
        PLANNER_MAX_OUTPUT_TOKENS,
        PLANNER_REASONING_EFFORT,
    ) == (10.0, 200, "none")
    assert client.max_retries == 2, "the shared client keeps its own retries"


async def test_a_tool_plan_is_returned_unchanged_and_logged_safely(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    result = await plan(Script(plan_response()))

    assert result == ToolPlan(tool_name="get_market_quote", symbol="msft")
    (completed,) = planning_events(caplog)
    assert completed == {
        "event": "planning.completed",
        "tool": "get_market_quote",
        "input_tokens": 321,
        "output_tokens": 45,
        "duration_ms": completed["duration_ms"],
    }
    assert isinstance(completed["duration_ms"], int)
    assert_no_planner_secret_logged(caplog)


async def test_a_null_plan_is_valid() -> None:
    result = await plan(Script(plan_response(NO_PLAN)))

    assert result == ToolPlan(tool_name=None, symbol=None)


@pytest.mark.parametrize(
    "error",
    [
        httpx2.Response(
            500, json={"error": {"message": f"upstream failure {FAKE_KEY}"}}
        ),
        httpx2.Response(429, json={"error": {"message": "upstream failure"}}),
        httpx2.Response(401, json={"error": {"message": "upstream failure"}}),
        httpx2.ConnectError(f"cannot reach host with key {FAKE_KEY}"),
    ],
    ids=["http-500", "http-429", "http-401", "connection-error"],
)
async def test_a_request_failure_is_one_attempt_and_safe(
    caplog: pytest.LogCaptureFixture, error: httpx2.Response | Exception
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(error)

    with pytest.raises(ToolPlanningError) as raised:
        await plan(script)

    assert script.calls == 1, "no SDK transport retry, although the client allows 2"
    assert raised.value.reason == "request_failed"
    assert raised.value.__cause__ is None and raised.value.__suppress_context__
    (failed,) = planning_events(caplog)
    assert failed["event"] == "planning.failed"
    assert failed["reason"] == "request_failed"
    assert isinstance(failed["error_type"], str)
    assert_no_planner_secret_logged(caplog)


def schema_invalid_plans() -> list[object]:
    return [
        {"tool_name": "fetch_url", "symbol": "MSFT"},
        {"tool_name": "get_market_quote"},
        {**QUOTE_PLAN, "url": "http://x"},
        {"tool_name": "get_market_quote", "symbol": 7},
        [QUOTE_PLAN],
    ]


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (non_json, "malformed_response"),
        (
            json_labelled(f"{{not json {PAYLOAD_SENTINEL}".encode()),
            "malformed_response",
        ),
        (malformed({**_BASE, "status": "completed"}), "malformed_response"),
        (
            lambda: response(
                status="incomplete", incomplete_reason="max_output_tokens"
            ),
            "incomplete_max_output_tokens",
        ),
        (
            lambda: response(status="incomplete", incomplete_reason="content_filter"),
            "incomplete_content_filter",
        ),
        (
            lambda: response(status="incomplete", incomplete_reason="max_messages"),
            "incomplete_other",
        ),
        (
            lambda: response(message(answer_text(QUOTE_PLAN)), status="failed"),
            "unexpected_status",
        ),
        (lambda: response(message(refusal())), "refusal"),
        (lambda: response(reasoning()), "no_output_text"),
        (
            lambda: response(message(answer_text(QUOTE_PLAN), answer_text(NO_PLAN))),
            "multiple_output_text",
        ),
        (lambda: response(message(text(f"{{{PAYLOAD_SENTINEL}"))), "invalid_json"),
        *(
            ((lambda payload=payload: plan_response(payload)), "schema_validation")
            for payload in schema_invalid_plans()
        ),
    ],
    ids=[
        "non-json",
        "json-labelled-invalid-json",
        "missing-output",
        "incomplete-max-output-tokens",
        "incomplete-content-filter",
        "incomplete-other",
        "unexpected-status",
        "refusal",
        "no-output-text",
        "multiple-output-text",
        "invalid-json",
        "schema-unknown-tool",
        "schema-missing-symbol",
        "schema-extra-field",
        "schema-wrong-type",
        "schema-not-an-object",
    ],
)
async def test_every_invalid_outcome_is_one_call_and_a_planning_error(
    caplog: pytest.LogCaptureFixture, build: Factory, reason: str
) -> None:
    caplog.set_level(logging.DEBUG)
    script = Script(build())

    with pytest.raises(ToolPlanningError) as raised:
        await plan(script)

    assert script.calls == 1, "no application retry, unlike the answer adapter"
    assert raised.value.reason == reason
    assert raised.value.__cause__ is None
    (failed,) = planning_events(caplog)
    assert (failed["event"], failed["reason"]) == ("planning.failed", reason)
    assert "error_type" not in failed
    assert generation_events(caplog) == planning_events(caplog), "no generation.*"
    assert_no_planner_secret_logged(caplog)


def test_a_planning_error_is_never_an_http_error() -> None:
    error = ToolPlanningError("refusal")

    assert not isinstance(error, AppError)
    assert (str(error), error.reason) == ("refusal", "refusal")


def test_the_plan_schema_constant_matches_the_pydantic_model() -> None:
    schema: Any = TOOL_PLAN_FORMAT["schema"]

    assert TOOL_PLAN_FORMAT["type"] == "json_schema"
    assert TOOL_PLAN_FORMAT["name"] == "tool_plan"
    assert TOOL_PLAN_FORMAT["strict"] is True
    assert schema["additionalProperties"] is False
    assert (
        schema["required"] == list(schema["properties"]) == list(ToolPlan.model_fields)
    )
    assert schema["properties"]["tool_name"] == {
        "type": ["string", "null"],
        "enum": [*get_args(PlannedToolName), None],
    }
    assert schema["properties"]["symbol"] == {"type": ["string", "null"]}


def test_the_planned_tool_names_are_exactly_the_mcp_allow_list() -> None:
    assert get_args(PlannedToolName) == get_args(MarketToolName)
    assert set(get_args(PlannedToolName)) == ALLOWED_TOOLS


@pytest.mark.parametrize(
    "payload",
    [
        {"tool_name": "fetch_url", "symbol": "MSFT"},
        {"tool_name": "get_market_quote", "symbol": "MSFT", "url": "http://x"},
        {"tool_name": "get_market_quote"},
        {"symbol": None},
        {"tool_name": 1, "symbol": None},
        {"tool_name": None, "symbol": 7},
    ],
    ids=[
        "unknown-tool",
        "extra-field",
        "missing-symbol",
        "missing-tool",
        "int-tool",
        "int-symbol",
    ],
)
def test_the_plan_model_rejects_off_contract_payloads(payload: object) -> None:
    with pytest.raises(ValidationError):
        ToolPlan.model_validate(payload)


def test_the_plan_model_accepts_both_fields_independently() -> None:
    """Pairing (both null or both present) is the graph's rule, not the model's."""
    assert (
        ToolPlan.model_validate({"tool_name": None, "symbol": "MSFT"}).symbol == "MSFT"
    )
    assert ToolPlan.model_validate(
        {"tool_name": "get_company_overview", "symbol": None}
    ).tool_name == ("get_company_overview")
