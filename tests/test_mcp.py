"""The MCP server across the real protocol boundary (DECISIONS section 20.4).

Server-side coverage: the actual ``build_mcp_server`` definition is reached
through the SDK's in-process ``Client`` in ``auto`` mode, which runs the same
request handlers as stdio. The provider is a scripted fake, so no test opens
a socket. The complete provider-classification matrix lives in
``test_market_data.py`` and is not repeated here.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import socket
import sys
from collections.abc import Iterator

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent

from app.config import MarketDataConfig
from app.market_data import (
    QUOTE_FRESHNESS,
    CompanyOverview,
    MarketDataError,
    MarketQuote,
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
from tests.fakes import ScriptedMarketDataProvider

SENTINEL = "AV-SENTINEL-KEY-7f3a"
LEAKY_MESSAGE = f"{SENTINEL} https://www.alphavantage.co/query?apikey={SENTINEL}"

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
