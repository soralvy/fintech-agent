"""The MCP server and client across the real protocol boundary.

DECISIONS section 20.4. The actual ``build_mcp_server`` definition is reached
through the SDK's in-process ``Client`` in ``auto`` mode, which runs the same
request handlers as stdio, and one offline test runs the real stdio entry
point. The provider is a scripted fake, so no in-process test opens a socket.
The complete provider-classification matrix lives in ``test_market_data.py``
and is not repeated here; this file covers representative cases only.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import socket
import sys
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, ClassVar, Self, cast, get_args

import anyio
import httpx
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    INTERNAL_ERROR,
    REQUEST_TIMEOUT,
    CallToolResult,
    ContentBlock,
    ImageContent,
    TextContent,
)

from app.config import MarketDataConfig
from app.logging import bind_request_id
from app.market_data import (
    PROVIDER_ERROR_CODES,
    QUOTE_FRESHNESS,
    AlphaVantageProvider,
    CompanyOverview,
    MarketDataError,
    MarketQuote,
)
from app.mcp_client import (
    ALLOWED_TOOLS,
    CLIENT_TIMEOUT_MARGIN_SECONDS,
    REPOSITORY_ROOT,
    TOOL_ARGUMENT_KEYS,
    TOOL_ERROR_CODES,
    MarketDataTools,
    ToolErrorCode,
    ToolFailure,
    ToolSuccess,
    open_market_data_tools,
    stdio_server_parameters,
    wire_error_code,
)
from app.mcp_server import (
    LOG_FORMAT,
    OVERVIEW_DESCRIPTION,
    QUOTE_DESCRIPTION,
    SERVER_NAME,
    TOOL_NAMES,
    _run_tool,
    build_mcp_server,
    configure_server_logging,
    main,
    serve,
)
from tests.fakes import (
    BarrierMarketDataProvider,
    ScriptedMarketDataProvider,
    alpha_vantage_transport,
    quote_for,
)

SENTINEL = "AV-SENTINEL-KEY-7f3a"
LEAKY_MESSAGE = f"{SENTINEL} https://www.alphavantage.co/query?apikey={SENTINEL}"
FIXTURES = Path(__file__).parent / "fixtures" / "alpha_vantage"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


QUOTE = MarketQuote(
    provider="alpha_vantage",
    symbol="MSFT",
    price="123.4500",
    previous_close="122.1000",
    change="1.3500",
    change_percent="1.1057%",
    volume="12345678",
    latest_trading_day="2026-09-23",
    freshness=QUOTE_FRESHNESS,
)
OVERVIEW = CompanyOverview(
    provider="alpha_vantage",
    symbol="MSFT",
    name="Example Corp",
    description="Makes things.",
    exchange="NASDAQ",
    currency="USD",
    sector="TECHNOLOGY",
    industry="SOFTWARE",
    market_capitalization="3100000000000",
    latest_quarter="2026-06-30",
)


def texts(result: CallToolResult) -> list[str]:
    return [block.text for block in result.content if isinstance(block, TextContent)]


def wire_error(tool: str, code: str) -> list[str]:
    return [f"Error executing tool {tool}: {code}"]


def client_for(provider: ScriptedMarketDataProvider) -> Client:
    return Client(build_mcp_server(provider), mode="auto", cache=None)


@pytest.fixture(autouse=True)
def restore_http_loggers() -> Iterator[None]:
    """``configure_server_logging`` changes global logger levels; undo it."""
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


# ---------------------------------------------------------------------------
# Server definition: exactly the two approved tools (AC9, AC10)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_server_exposes_exactly_the_two_approved_tools() -> None:
    async with client_for(ScriptedMarketDataProvider()) as client:
        tools = (await client.list_tools()).tools
        resources = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resource_templates
        prompts = (await client.list_prompts()).prompts

    assert sorted(tool.name for tool in tools) == sorted(TOOL_NAMES)
    assert resources == []
    assert templates == []
    assert prompts == []


@pytest.mark.anyio
async def test_tool_schemas_descriptions_and_annotations() -> None:
    async with client_for(ScriptedMarketDataProvider()) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    expected = {
        "get_market_quote": (QUOTE_DESCRIPTION, "MarketQuote"),
        "get_company_overview": (OVERVIEW_DESCRIPTION, "CompanyOverview"),
    }
    for name, (description, output_title) in expected.items():
        tool = tools[name]
        assert tool.description == description
        assert "Provider data" in description
        assert "end-of-day" in description
        assert tool.input_schema["properties"].keys() == {"symbol"}
        assert tool.input_schema["properties"]["symbol"]["type"] == "string"
        assert tool.input_schema["required"] == ["symbol"]
        assert tool.output_schema is not None
        assert tool.output_schema["title"] == output_title
        assert tool.output_schema["additionalProperties"] is False
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.idempotent_hint is True
        assert tool.annotations.open_world_hint is True

    quote_price = tools["get_market_quote"].output_schema
    assert quote_price is not None
    assert quote_price["properties"]["price"]["type"] == "string"


# ---------------------------------------------------------------------------
# Success across the boundary (AC12, server side)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_both_tools_return_validated_structured_output() -> None:
    provider = ScriptedMarketDataProvider(quote=QUOTE, overview=OVERVIEW)

    async with client_for(provider) as client:
        quote = await client.call_tool("get_market_quote", {"symbol": "MSFT"})
        overview = await client.call_tool("get_company_overview", {"symbol": "MSFT"})

    assert quote.is_error is False
    assert quote.structured_content == QUOTE.model_dump()
    assert MarketQuote.model_validate(quote.structured_content) == QUOTE
    assert overview.is_error is False
    assert overview.structured_content == OVERVIEW.model_dump()
    assert CompanyOverview.model_validate(overview.structured_content) == OVERVIEW


@pytest.mark.anyio
@pytest.mark.parametrize("raw", ["msft", " msft ", "\tMsFt\n"])
async def test_the_normalized_symbol_reaches_the_provider(raw: str) -> None:
    provider = ScriptedMarketDataProvider(quote=QUOTE, overview=OVERVIEW)

    async with client_for(provider) as client:
        await client.call_tool("get_market_quote", {"symbol": raw})
        await client.call_tool("get_company_overview", {"symbol": raw})

    assert provider.calls == [("get_quote", "MSFT"), ("get_overview", "MSFT")]


# ---------------------------------------------------------------------------
# Failures across the boundary (AC1, AC5, AC6)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("tool", TOOL_NAMES)
@pytest.mark.parametrize("raw", ["BAD SYMBOL", "", "ﬁ", "A" * 16, "../etc"])
async def test_invalid_input_is_rejected_before_the_provider(
    tool: str, raw: str
) -> None:
    provider = ScriptedMarketDataProvider(quote=QUOTE, overview=OVERVIEW)

    async with client_for(provider) as client:
        result = await client.call_tool(tool, {"symbol": raw})

    assert result.is_error is True
    assert result.structured_content is None
    assert texts(result) == wire_error(tool, "invalid_input")
    assert provider.calls == []


@pytest.mark.anyio
async def test_a_representative_provider_error_crosses_the_boundary() -> None:
    provider = ScriptedMarketDataProvider(quote=MarketDataError("rate_limited"))

    async with client_for(provider) as client:
        result = await client.call_tool("get_market_quote", {"symbol": "MSFT"})

    assert result.is_error is True
    assert result.structured_content is None
    assert texts(result) == wire_error("get_market_quote", "rate_limited")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unexpected",
    [
        RuntimeError(LEAKY_MESSAGE),
        ValueError(LEAKY_MESSAGE),
        OSError(LEAKY_MESSAGE),
        KeyError(SENTINEL),
    ],
    ids=["runtime", "value", "os", "key"],
)
async def test_an_unexpected_provider_exception_is_sanitized(
    unexpected: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    provider = ScriptedMarketDataProvider(overview=unexpected)

    async with client_for(provider) as client:
        result = await client.call_tool("get_company_overview", {"symbol": "MSFT"})

    assert result.is_error is True
    assert result.structured_content is None
    assert texts(result) == wire_error("get_company_overview", "provider_unavailable")
    for block in result.content:
        assert SENTINEL not in block.model_dump_json()
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert "alphavantage" not in record.getMessage()
        assert record.levelno < logging.ERROR
        assert record.exc_info is None


# ---------------------------------------------------------------------------
# The sanitizing boundary itself
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("symbol", "outcome", "code"),
    [
        ("BAD SYMBOL", None, "invalid_input"),
        ("MSFT", MarketDataError("timeout"), "timeout"),
        ("MSFT", RuntimeError(LEAKY_MESSAGE), "provider_unavailable"),
    ],
    ids=["invalid-symbol", "market-data-error", "unexpected"],
)
async def test_the_boundary_raises_an_unchained_tool_error(
    symbol: str, outcome: Exception | None, code: str
) -> None:
    provider = ScriptedMarketDataProvider(quote=outcome if outcome else QUOTE)

    with pytest.raises(ToolError) as raised:
        await _run_tool(provider.get_quote, symbol)

    assert str(raised.value) == code
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert SENTINEL not in repr(raised.value)


@pytest.mark.anyio
async def test_cancellation_propagates_through_the_boundary() -> None:
    async def cancelled(symbol: str) -> MarketQuote:
        raise anyio.get_cancelled_exc_class()

    with pytest.raises(asyncio.CancelledError):
        await _run_tool(cancelled, "MSFT")


# ---------------------------------------------------------------------------
# Import, construction, and logging have no hidden side effects
# ---------------------------------------------------------------------------


def test_import_and_construction_perform_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level

    # Execute a fresh, unregistered copy of the module, so the imported one
    # (and everything other tests hold) is left untouched.
    spec = importlib.util.find_spec("app.mcp_server")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    provider = ScriptedMarketDataProvider()
    module.build_mcp_server(provider)

    assert provider.calls == []
    assert root.handlers == handlers_before
    assert root.level == level_before
    assert not any(
        type(value).__name__ == "MCPServer" for value in vars(module).values()
    )


def test_server_logging_is_configured_before_the_server() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = list(root.handlers), root.level
    logging.getLogger("httpx").setLevel(logging.DEBUG)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)
    # pytest attaches its capture handlers to the root logger for the test
    # call, so they are removed here, inside the test, and restored after.
    root.handlers.clear()
    try:
        configure_server_logging()
        build_mcp_server(ScriptedMarketDataProvider())
        handlers = list(root.handlers)
        root_level = root.level
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)

    # The SDK's own basicConfig found our handler and added nothing.
    assert len(handlers) == 1
    handler = handlers[0]
    assert type(handler) is logging.StreamHandler
    assert handler.stream is sys.stderr
    assert handler.formatter is not None
    assert handler.formatter._fmt == LOG_FORMAT
    assert root_level == logging.WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def refuse_to_serve(*args: object) -> None:
    raise AssertionError("the server must not start")


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "ALPHA_VANTAGE_API_KEY is not set"),
        ({"ALPHA_VANTAGE_API_KEY": "   "}, "ALPHA_VANTAGE_API_KEY is not set"),
        (
            {"ALPHA_VANTAGE_API_KEY": SENTINEL, "MCP_TOOL_TIMEOUT_SECONDS": SENTINEL},
            "MCP_TOOL_TIMEOUT_SECONDS must be a number greater than 0 and at most 30",
        ),
        (
            {"ALPHA_VANTAGE_API_KEY": SENTINEL, "MCP_TOOL_TIMEOUT_SECONDS": "31"},
            "MCP_TOOL_TIMEOUT_SECONDS must be a number greater than 0 and at most 30",
        ),
    ],
    ids=["unset-key", "blank-key", "non-numeric-timeout", "timeout-too-large"],
)
def test_a_config_error_exits_2_with_one_safe_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    message: str,
) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    monkeypatch.delenv("MCP_TOOL_TIMEOUT_SECONDS", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(anyio, "run", refuse_to_serve)

    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert captured.err == f"{SERVER_NAME}: {message}\n"
    assert captured.out == ""
    assert SENTINEL not in captured.err


def test_a_valid_config_runs_serve_with_anyio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", SENTINEL)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", "7.5")
    started: list[tuple[object, ...]] = []

    def record(*args: object) -> None:
        started.append(args)

    monkeypatch.setattr(anyio, "run", record)

    main()

    assert started == [(serve, MarketDataConfig(api_key=SENTINEL, timeout_seconds=7.5))]


# ===========================================================================
# The standalone application client (Stage D)
# ===========================================================================


def tools_for(provider: ScriptedMarketDataProvider) -> Any:
    return open_market_data_tools(build_mcp_server(provider), timeout_seconds=5.0)


class StubCaller:
    """A ``ToolCaller`` that returns or raises one scripted outcome."""

    def __init__(self, outcome: CallToolResult | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[tuple[str, dict[str, Any] | None, float | None]] = []

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult:
        self.calls.append((name, arguments, read_timeout_seconds))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def structured(content: dict[str, Any] | None) -> CallToolResult:
    return CallToolResult(content=[], structured_content=content, is_error=False)


def error_result(*blocks: ContentBlock) -> CallToolResult:
    return CallToolResult(content=list(blocks), is_error=True)


def text(value: str) -> TextContent:
    return TextContent(type="text", text=value)


def client_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "app.mcp_client"
    ]


def test_the_client_constants_are_closed() -> None:
    assert ALLOWED_TOOLS == set(TOOL_NAMES)
    assert TOOL_ARGUMENT_KEYS == {"symbol"}
    assert TOOL_ERROR_CODES == PROVIDER_ERROR_CODES | {"tool_not_allowed"}
    assert CLIENT_TIMEOUT_MARGIN_SECONDS == 2.0
    # TOOL_ERROR_CODES is the runtime twin of ToolErrorCode; they must agree.
    assert set(get_args(ToolErrorCode)) == TOOL_ERROR_CODES


@pytest.mark.parametrize(
    ("tool", "code"),
    [("get_market_quote", SENTINEL), (None, "unknown"), ("get_prices", "timeout")],
)
def test_tool_failure_rejects_unknown_values_without_echo(
    tool: str | None, code: str
) -> None:
    with pytest.raises(ValueError) as raised:
        ToolFailure(cast(Any, tool), cast(Any, code))

    assert SENTINEL not in str(raised.value)
    assert "get_prices" not in str(raised.value)


def test_results_are_frozen() -> None:
    failure = ToolFailure("get_market_quote", "timeout")
    success = ToolSuccess("get_market_quote", QUOTE)
    field = "error_code"
    with pytest.raises(AttributeError):
        setattr(failure, field, "no_data")
    field = "result"
    with pytest.raises(AttributeError):
        setattr(success, field, OVERVIEW)


# ---------------------------------------------------------------------------
# Success and representative failure across the boundary (AC12)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_client_returns_validated_results_for_both_tools() -> None:
    provider = ScriptedMarketDataProvider(quote=QUOTE, overview=OVERVIEW)

    async with tools_for(provider) as tools:
        quote = await tools.call("get_market_quote", {"symbol": " msft "})
        overview = await tools.call("get_company_overview", {"symbol": "MSFT"})

    assert quote == ToolSuccess("get_market_quote", QUOTE)
    assert overview == ToolSuccess("get_company_overview", OVERVIEW)
    assert provider.calls == [("get_quote", "MSFT"), ("get_overview", "MSFT")]


@pytest.mark.anyio
async def test_a_representative_provider_error_reaches_the_client() -> None:
    provider = ScriptedMarketDataProvider(quote=MarketDataError("rate_limited"))

    async with tools_for(provider) as tools:
        result = await tools.call("get_market_quote", {"symbol": "MSFT"})

    assert result == ToolFailure("get_market_quote", "rate_limited")


@pytest.mark.anyio
async def test_an_unexpected_provider_exception_reaches_the_client_sanitized() -> None:
    provider = ScriptedMarketDataProvider(overview=RuntimeError(LEAKY_MESSAGE))

    async with tools_for(provider) as tools:
        result = await tools.call("get_company_overview", {"symbol": "MSFT"})

    assert result == ToolFailure("get_company_overview", "provider_unavailable")
    assert SENTINEL not in repr(result)


@pytest.mark.anyio
async def test_the_real_adapter_composes_through_every_layer() -> None:
    """The full chain: client -> server -> adapter -> symbol validator.

    Each layer is otherwise tested in isolation (against a scripted
    provider here, or a mocked transport in ``test_market_data.py``). This
    is the one test where ``MarketDataTools`` reaches a real
    ``AlphaVantageProvider`` over ``build_mcp_server``, with only the HTTP
    transport faked, proving the ``MarketDataProvider`` Protocol boundary
    is not just type-checked but actually wired end to end.
    """

    async def respond(request: httpx.Request) -> httpx.Response:
        function = request.url.params["function"]
        body = fixture_bytes(
            "global_quote_ok" if function == "GLOBAL_QUOTE" else "overview_ok"
        )
        return httpx.Response(200, content=body)

    recording = alpha_vantage_transport(respond)
    config = MarketDataConfig(api_key=SENTINEL)
    async with httpx.AsyncClient(
        transport=recording.transport, follow_redirects=False
    ) as http_client:
        provider = AlphaVantageProvider(http_client, config)
        async with open_market_data_tools(
            build_mcp_server(provider), timeout_seconds=5.0
        ) as tools:
            quote = await tools.call("get_market_quote", {"symbol": " msft "})
            overview = await tools.call("get_company_overview", {"symbol": "msft"})

    assert isinstance(quote, ToolSuccess)
    assert isinstance(quote.result, MarketQuote)
    assert quote.tool == "get_market_quote"
    assert quote.result.symbol == "MSFT"
    assert quote.result.price == "123.4500"
    assert isinstance(overview, ToolSuccess)
    assert isinstance(overview.result, CompanyOverview)
    assert overview.tool == "get_company_overview"
    assert overview.result.name == "Example Corp"
    assert [request.url.params["symbol"] for request in recording.requests] == [
        "MSFT",
        "MSFT",
    ]


# ---------------------------------------------------------------------------
# Arguments, allow-list, and the rebuilt request (AC1, AC10)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_request_is_rebuilt_with_the_client_deadline() -> None:
    caller = StubCaller(structured(QUOTE.model_dump()))
    tools = MarketDataTools(caller, timeout_seconds=5.0)

    result = await tools.call("get_market_quote", {"symbol": " msft "})

    assert result == ToolSuccess("get_market_quote", QUOTE)
    assert caller.calls == [("get_market_quote", {"symbol": "MSFT"}, 7.0)]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "arguments",
    [
        {"symbol": "MSFT", "url": "https://example.invalid"},
        {"symbol": "MSFT", "apikey": SENTINEL},
        {},
        {"Symbol": "MSFT"},
        {1: "MSFT"},
        {"symbol": "BAD SYMBOL"},
        {"symbol": SENTINEL},
        {"symbol": "ﬁ"},
        {"symbol": 42},
        {"symbol": None},
        ["symbol"],
        "MSFT",
    ],
    ids=[
        "extra-url",
        "extra-key",
        "empty",
        "wrong-case",
        "non-string-key",
        "invalid-symbol",
        "too-long",
        "non-ascii",
        "int",
        "none",
        "list",
        "string",
    ],
)
async def test_inexact_arguments_are_invalid_input_with_no_call(
    arguments: object,
) -> None:
    caller = StubCaller(structured(QUOTE.model_dump()))
    tools = MarketDataTools(caller, timeout_seconds=5.0)

    result = await tools.call("get_market_quote", cast(Mapping[str, object], arguments))

    assert result == ToolFailure("get_market_quote", "invalid_input")
    assert caller.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "tool_name", ["get_prices", "", "GET_MARKET_QUOTE", SENTINEL, "get_market_quote "]
)
async def test_a_disallowed_tool_is_rejected_with_no_call(tool_name: str) -> None:
    caller = StubCaller(structured(QUOTE.model_dump()))
    tools = MarketDataTools(caller, timeout_seconds=5.0)

    result = await tools.call(tool_name, {"symbol": "MSFT"})

    assert result == ToolFailure(None, "tool_not_allowed")
    assert caller.calls == []


# ---------------------------------------------------------------------------
# Error text: exact closed codes only (AC6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", sorted(PROVIDER_ERROR_CODES))
def test_the_wire_parser_accepts_every_closed_code(code: str) -> None:
    content = [text(f"Error executing tool get_market_quote: {code}")]

    assert wire_error_code("get_market_quote", content) == code


@pytest.mark.parametrize(
    "content",
    [
        [],
        [text("Error executing tool get_market_quote")],
        [text("Error executing tool get_market_quote: something else")],
        [text("Error executing tool get_market_quote: tool_not_allowed")],
        [text("Error executing tool get_company_overview: rate_limited")],
        [text("Error executing tool get_market_quote: rate_limited ")],
        [text(" Error executing tool get_market_quote: rate_limited")],
        [text("Error executing tool get_market_quote: RATE_LIMITED")],
        [text("Unknown tool: get_market_quote")],
        [text(f"Error executing tool get_market_quote: {LEAKY_MESSAGE}")],
        [ImageContent(type="image", data="aGk=", mime_type="image/png")],
        [
            ImageContent(type="image", data="aGk=", mime_type="image/png"),
            text("Error executing tool get_market_quote: rate_limited"),
        ],
    ],
    ids=[
        "no-content",
        "generic-crash-text",
        "unknown-code",
        "client-only-code",
        "other-tool",
        "trailing-space",
        "leading-space",
        "wrong-case",
        "unknown-tool",
        "leaky-text",
        "non-text-block",
        "non-text-first-block",
    ],
)
def test_any_other_error_content_is_provider_unavailable(
    content: list[ContentBlock],
) -> None:
    assert wire_error_code("get_market_quote", content) == "provider_unavailable"


@pytest.mark.anyio
async def test_unknown_error_text_from_a_real_server_is_provider_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rogue: MCPServer[Any] = MCPServer("rogue", log_level="WARNING")

    @rogue.tool(name="get_market_quote", structured_output=True)
    async def get_market_quote(symbol: str) -> MarketQuote:
        raise ToolError(f"something else {SENTINEL}")

    async with open_market_data_tools(rogue, timeout_seconds=5.0) as tools:
        result = await tools.call("get_market_quote", {"symbol": "MSFT"})

    assert result == ToolFailure("get_market_quote", "provider_unavailable")
    assert all(SENTINEL not in json.dumps(event) for event in client_events(caplog))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "outcome",
    [
        error_result(),
        error_result(text("no prefix at all")),
        error_result(ImageContent(type="image", data="aGk=", mime_type="image/png")),
    ],
    ids=["no-blocks", "unknown-text", "non-text"],
)
async def test_error_results_without_a_known_code_are_provider_unavailable(
    outcome: CallToolResult,
) -> None:
    tools = MarketDataTools(StubCaller(outcome), timeout_seconds=5.0)

    result = await tools.call("get_market_quote", {"symbol": "MSFT"})

    assert result == ToolFailure("get_market_quote", "provider_unavailable")


# ---------------------------------------------------------------------------
# Strict structured output (AC4)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("tool", "content"),
    [
        ("get_market_quote", None),
        ("get_market_quote", {}),
        ("get_market_quote", {**QUOTE.model_dump(), "apikey": SENTINEL}),
        ("get_market_quote", {**QUOTE.model_dump(), "price": 123.45}),
        ("get_market_quote", {**QUOTE.model_dump(), "freshness": "real-time"}),
        ("get_market_quote", {**QUOTE.model_dump(), "symbol": "AAPL"}),
        ("get_market_quote", OVERVIEW.model_dump()),
        ("get_company_overview", QUOTE.model_dump()),
        ("get_company_overview", {**OVERVIEW.model_dump(), "symbol": "msft"}),
    ],
    ids=[
        "missing",
        "empty",
        "extra-field",
        "number-not-string",
        "wrong-freshness",
        "symbol-mismatch",
        "overview-for-quote",
        "quote-for-overview",
        "non-canonical-symbol",
    ],
)
async def test_invalid_structured_output_is_malformed(
    tool: str, content: dict[str, Any] | None
) -> None:
    tools = MarketDataTools(StubCaller(structured(content)), timeout_seconds=5.0)

    result = await tools.call(tool, {"symbol": "MSFT"})

    assert result == ToolFailure(cast(Any, tool), "malformed_provider_response")


@pytest.mark.anyio
async def test_a_real_server_returning_another_symbol_is_malformed() -> None:
    rogue: MCPServer[Any] = MCPServer("rogue", log_level="WARNING")

    @rogue.tool(name="get_market_quote", structured_output=True)
    async def get_market_quote(symbol: str) -> MarketQuote:
        return QUOTE.model_copy(update={"symbol": "AAPL"})

    async with open_market_data_tools(rogue, timeout_seconds=5.0) as tools:
        result = await tools.call("get_market_quote", {"symbol": "MSFT"})

    assert result == ToolFailure("get_market_quote", "malformed_provider_response")


# ---------------------------------------------------------------------------
# Deadline and exception mapping (AC8, §12.1)
# ---------------------------------------------------------------------------


class SlowProvider:
    """A provider whose calls never finish within the client deadline."""

    async def get_quote(self, symbol: str) -> MarketQuote:
        await anyio.sleep(60)
        return QUOTE

    async def get_overview(self, symbol: str) -> CompanyOverview:
        await anyio.sleep(60)
        return OVERVIEW


@pytest.mark.anyio
async def test_a_stalled_server_hits_the_client_deadline() -> None:
    started = time.monotonic()

    async with open_market_data_tools(
        build_mcp_server(SlowProvider()), timeout_seconds=0.05
    ) as tools:
        result = await tools.call("get_market_quote", {"symbol": "MSFT"})
        elapsed = time.monotonic() - started

    assert result == ToolFailure("get_market_quote", "timeout")
    # The deadline is timeout_seconds + 2.0 = 2.05 s.
    assert 2.0 <= elapsed < 10.0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (MCPError(REQUEST_TIMEOUT, "timed out"), "timeout"),
        (TimeoutError(), "timeout"),
        (MCPError(CONNECTION_CLOSED, LEAKY_MESSAGE), "provider_unavailable"),
        (MCPError(INTERNAL_ERROR, LEAKY_MESSAGE), "provider_unavailable"),
        (
            RuntimeError(
                "Invalid structured content returned by tool get_market_quote: "
                + LEAKY_MESSAGE
            ),
            "malformed_provider_response",
        ),
        (
            RuntimeError(
                "Tool get_market_quote has an output schema but did not return "
                "structured content"
            ),
            "malformed_provider_response",
        ),
        (RuntimeError(LEAKY_MESSAGE), "provider_unavailable"),
        (ValueError(LEAKY_MESSAGE), "provider_unavailable"),
        (anyio.ClosedResourceError(), "provider_unavailable"),
        (anyio.BrokenResourceError(), "provider_unavailable"),
        (OSError(LEAKY_MESSAGE), "provider_unavailable"),
    ],
    ids=[
        "mcp-request-timeout",
        "timeout-error",
        "mcp-connection-closed",
        "mcp-internal-error",
        "sdk-invalid-structured-content",
        "sdk-missing-structured-content",
        "other-runtime-error",
        "value-error",
        "closed-resource",
        "broken-resource",
        "os-error",
    ],
)
async def test_expected_exceptions_map_to_closed_codes(
    error: Exception, expected: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tools = MarketDataTools(StubCaller(error), timeout_seconds=5.0)

    result = await tools.call("get_market_quote", {"symbol": "MSFT"})

    assert result == ToolFailure("get_market_quote", cast(Any, expected))
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert record.exc_info is None


@pytest.mark.anyio
async def test_cancellation_propagates_through_the_client() -> None:
    tools = MarketDataTools(
        StubCaller(anyio.get_cancelled_exc_class()()), timeout_seconds=5.0
    )

    with pytest.raises(asyncio.CancelledError):
        await tools.call("get_market_quote", {"symbol": "MSFT"})


# ---------------------------------------------------------------------------
# Lifecycle (AC13)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_the_client_opens_and_closes_in_one_async_context() -> None:
    provider = ScriptedMarketDataProvider(quote=QUOTE)

    async with tools_for(provider) as tools:
        assert isinstance(tools, MarketDataTools)
        assert await tools.call("get_market_quote", {"symbol": "MSFT"}) == ToolSuccess(
            "get_market_quote", QUOTE
        )

    # After exit the connection is closed: a call fails safely, never raises.
    assert await tools.call("get_market_quote", {"symbol": "MSFT"}) == ToolFailure(
        "get_market_quote", "provider_unavailable"
    )
    assert provider.calls == [("get_quote", "MSFT")]


@pytest.mark.anyio
async def test_one_connection_carries_overlapping_calls_from_separate_tasks() -> None:
    """M6 D19 (T20): the shared client needs no lock.

    Every provider call waits until all four are in flight, so calls that
    were serialized would fail at the barrier's deadline instead of passing.
    """
    parties = 4
    provider = BarrierMarketDataProvider(parties, deadline_seconds=5.0)
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    results: dict[str, ToolSuccess | ToolFailure] = {}

    async with tools_for_provider(provider) as tools:

        async def call(symbol: str) -> None:
            arguments = {"symbol": symbol.lower()}
            results[symbol] = await tools.call("get_market_quote", arguments)

        async with anyio.create_task_group() as group:
            for symbol in symbols:
                group.start_soon(call, symbol)

    assert results == {
        symbol: ToolSuccess("get_market_quote", quote_for(symbol)) for symbol in symbols
    }
    assert provider.barrier_reached
    assert provider.max_active == parties
    assert sorted(provider.calls) == [("get_quote", symbol) for symbol in symbols]


def tools_for_provider(
    provider: BarrierMarketDataProvider,
) -> AbstractAsyncContextManager[MarketDataTools]:
    return open_market_data_tools(build_mcp_server(provider), timeout_seconds=5.0)


# ---------------------------------------------------------------------------
# Events (AC15)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_client_events_carry_only_the_safe_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    provider = ScriptedMarketDataProvider(
        quote=QUOTE, overview=MarketDataError("rate_limited")
    )

    with bind_request_id("req-m5"):
        async with tools_for(provider) as tools:
            await tools.call("get_market_quote", {"symbol": " msft "})
            await tools.call("get_market_quote", {"symbol": SENTINEL})
            await tools.call(SENTINEL, {"symbol": "MSFT"})
            await tools.call("get_company_overview", {"symbol": "MSFT"})

    events = client_events(caplog)
    assert [event["event"] for event in events] == [
        "mcp.tool.requested",
        "mcp.tool.completed",
        "mcp.tool.failed",
        "mcp.tool.failed",
        "mcp.tool.requested",
        "mcp.tool.failed",
    ]
    requested, completed, invalid, disallowed, _, provider_failure = events

    base = {"request_id", "provider", "tool", "symbol", "event"}
    assert requested.keys() == base
    assert completed.keys() == base | {"duration_ms"}
    for failed in (invalid, disallowed, provider_failure):
        assert failed.keys() == base | {"duration_ms", "error_code"}
    for event in events:
        assert event["request_id"] == "req-m5"
        assert event["provider"] == "alpha_vantage"
    for event in (completed, invalid, disallowed, provider_failure):
        assert isinstance(event["duration_ms"], int)
        assert event["duration_ms"] >= 0

    assert requested["tool"] == "get_market_quote"
    assert requested["symbol"] == "MSFT"
    assert invalid["tool"] == "get_market_quote"
    assert invalid["symbol"] is None
    assert invalid["error_code"] == "invalid_input"
    assert disallowed["tool"] is None
    assert disallowed["symbol"] is None
    assert disallowed["error_code"] == "tool_not_allowed"
    assert provider_failure["error_code"] == "rate_limited"
    assert provider_failure["symbol"] == "MSFT"
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()


# ---------------------------------------------------------------------------
# Stdio: the production transport and entry point (AC9, AC11, AC13)
# ---------------------------------------------------------------------------


def test_stdio_parameters_pass_only_the_required_values() -> None:
    params = stdio_server_parameters(
        MarketDataConfig(api_key=SENTINEL, timeout_seconds=7.5)
    )

    assert params.command == sys.executable
    assert params.args == ["-m", "app.mcp_server"]
    assert params.cwd == REPOSITORY_ROOT
    assert (REPOSITORY_ROOT / "app" / "mcp_server.py").is_file()
    assert params.env == {
        "ALPHA_VANTAGE_API_KEY": SENTINEL,
        "MCP_TOOL_TIMEOUT_SECONDS": "7.5",
    }


def no_child_process_remains() -> bool:
    """True when this process has no child left; ``WNOHANG`` never blocks."""
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        return True
    return False


@pytest.mark.anyio
async def test_a_child_processs_stderr_can_be_captured_to_a_file(
    tmp_path: Path,
) -> None:
    """Positive control for the next test.

    ``mcp.client.stdio.stdio_client`` defaults its ``errlog`` parameter to
    ``sys.stderr`` at import time, which is not pytest's per-test capture fd,
    so a plain ``Client(StdioServerParameters(...))`` cannot prove the child's
    stderr is clean: ``capfd`` never sees it. Passing ``stdio_client(params,
    errlog=<file>)`` as the transport does, which this test confirms before
    the leak-absence assertion relies on it.
    """
    errlog_path = tmp_path / "child_stderr.log"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", f"import sys; sys.stderr.write({LEAKY_MESSAGE!r})"],
    )

    with (
        errlog_path.open("w", encoding="utf-8") as errlog,
        pytest.raises(BaseException),  # noqa: B017 - no handshake reply for a bad command
    ):
        async with Client(
            stdio_client(params, errlog=errlog),
            mode="auto",
            cache=None,
            read_timeout_seconds=2,
        ):
            pass

    assert errlog_path.read_text(encoding="utf-8") == LEAKY_MESSAGE


@pytest.mark.anyio
async def test_the_real_entry_point_serves_over_stdio(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The one offline stdio test: a real ``python -m app.mcp_server`` child.

    Only a symbol that fails validation is sent, and every proxy variable
    points at a closed local port, so no external/provider request can
    succeed; a broken implementation could at most try a loopback connection.
    The child's stderr is captured to a file (see the previous test), not
    relied on through ``capfd``, which does not see it.
    """
    params = stdio_server_parameters(MarketDataConfig(api_key=SENTINEL))
    sink = "http://127.0.0.1:9"
    env = {
        **(params.env or {}),
        "HTTP_PROXY": sink,
        "HTTPS_PROXY": sink,
        "ALL_PROXY": sink,
        "NO_PROXY": "",
    }
    params = params.model_copy(update={"env": env})
    errlog_path = tmp_path / "mcp_server_stderr.log"

    with errlog_path.open("w", encoding="utf-8") as errlog:
        async with Client(
            stdio_client(params, errlog=errlog),
            mode="auto",
            cache=None,
            read_timeout_seconds=30,
        ) as client:
            protocol_version = client.protocol_version
            listed = sorted(tool.name for tool in (await client.list_tools()).tools)
            result = await client.call_tool(
                "get_market_quote", {"symbol": "BAD SYMBOL"}
            )

    assert protocol_version == "2026-07-28"
    assert listed == sorted(TOOL_NAMES)
    assert result.is_error is True
    assert texts(result) == wire_error("get_market_quote", "invalid_input")
    # The SDK has reaped the child: no child process of this test remains.
    assert no_child_process_remains()
    child_stderr = errlog_path.read_text(encoding="utf-8")
    assert SENTINEL not in child_stderr
    captured = capfd.readouterr()
    assert SENTINEL not in captured.err
    assert SENTINEL not in captured.out


# ---------------------------------------------------------------------------
# The errlog parameter (M6 D5, T28)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_errlog_receives_a_stdio_childs_stderr(tmp_path: Path) -> None:
    """A ``python -c`` child, not the server: its stderr reaches the given file."""
    errlog_path = tmp_path / "child_stderr.log"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", f"import sys; sys.stderr.write({LEAKY_MESSAGE!r})"],
    )

    with (
        errlog_path.open("w", encoding="utf-8") as errlog,
        pytest.raises(BaseException),  # noqa: B017 - no handshake reply for a bad command
    ):
        async with open_market_data_tools(params, timeout_seconds=0.5, errlog=errlog):
            pass

    assert errlog_path.read_text(encoding="utf-8") == LEAKY_MESSAGE


class RecordingClient:
    """Stands in for ``mcp.Client`` and records how it was constructed."""

    instances: ClassVar[list[RecordingClient]] = []

    def __init__(self, server: object, **options: object) -> None:
        self.server = server
        self.options = options
        RecordingClient.instances.append(self)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


@pytest.fixture
def recording_client(monkeypatch: pytest.MonkeyPatch) -> list[RecordingClient]:
    RecordingClient.instances = []
    monkeypatch.setattr("app.mcp_client.Client", RecordingClient)
    return RecordingClient.instances


@pytest.mark.anyio
async def test_without_errlog_the_server_is_passed_through_unchanged(
    recording_client: list[RecordingClient],
) -> None:
    params = stdio_server_parameters(MarketDataConfig(api_key=SENTINEL))
    server = build_mcp_server(ScriptedMarketDataProvider())

    for target in (params, server):
        async with open_market_data_tools(target, timeout_seconds=5.0) as tools:
            assert isinstance(tools, MarketDataTools)

    assert [client.server for client in recording_client] == [params, server]
    expected = {"mode": "auto", "cache": None, "read_timeout_seconds": 7.0}
    assert [client.options for client in recording_client] == [expected, expected]


@pytest.mark.anyio
async def test_errlog_wraps_only_a_stdio_server(
    recording_client: list[RecordingClient],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    transport = object()
    wrapped: list[tuple[object, object]] = []

    def fake_stdio_client(server: object, errlog: object) -> object:
        wrapped.append((server, errlog))
        return transport

    monkeypatch.setattr("app.mcp_client.stdio_client", fake_stdio_client)
    params = stdio_server_parameters(MarketDataConfig(api_key=SENTINEL))
    server = build_mcp_server(ScriptedMarketDataProvider())

    with (tmp_path / "errlog").open("w", encoding="utf-8") as errlog:
        for target in (params, server):
            async with open_market_data_tools(
                target, timeout_seconds=5.0, errlog=errlog
            ):
                pass

    assert wrapped == [(params, errlog)]
    stdio, in_process = recording_client
    assert stdio.server is transport
    assert in_process.server is server, "errlog does not apply in-process"
    expected = {"mode": "auto", "cache": None, "read_timeout_seconds": 7.0}
    assert stdio.options == in_process.options == expected
