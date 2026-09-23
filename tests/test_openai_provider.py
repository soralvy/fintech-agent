"""The OpenAI embedding adapter against the real SDK over a mock transport.

The SDK parses real HTTP responses, but every request is answered in-process
by ``httpx2.MockTransport``; nothing reaches the network and the key is fake.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx2
import pytest
from openai import AsyncOpenAI

from app.errors import EmbeddingProviderError
from app.openai_provider import OpenAIEmbedder

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
