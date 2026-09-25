"""The application-side MCP client for the market-data tools (DECISIONS section 14).

The API lifespan opens one shared connection through ``open_market_data_tools``
and hands the resulting ``MarketDataTools`` to the graph, whose ``call_tool``
node is its only caller (docs/DECISIONS.md section 14).

``MarketDataTools.call`` makes at most one ``tools/call`` request and never
raises for an expected failure. It enforces the tool allow-list and the exact
argument keys (the pinned ``mcp`` 2.2.0 silently ignores unknown arguments),
validates the symbol with the canonical rule before any call, and rebuilds
the arguments itself. Every outcome maps to a closed code by the table in
docs/changes/M5-mcp-server.md section 12.1. MCP error text, exception
messages, and exception representations are never logged, returned, or kept;
unknown error text is ``provider_unavailable``. Cancellation is never caught.

The client reaches the server only across the protocol: over stdio in
production, or an in-process server object in tests. It never imports the
server module.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, Protocol, TextIO, cast, get_args

import anyio
from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.server import MCPServer
from mcp.shared.exceptions import MCPError
from mcp.types import REQUEST_TIMEOUT, CallToolResult, ContentBlock, TextContent
from pydantic import ValidationError

import app
from app.config import (
    ALPHA_VANTAGE_API_KEY_ENV,
    MCP_TOOL_TIMEOUT_SECONDS_ENV,
    MarketDataConfig,
)
from app.logging import EventField, log_event
from app.market_data import (
    PROVIDER,
    PROVIDER_ERROR_CODES,
    CompanyOverview,
    MarketQuote,
    ProviderErrorCode,
)
from app.symbols import InvalidSymbolError, normalize_symbol

logger = logging.getLogger(__name__)

MarketToolName = Literal["get_market_quote", "get_company_overview"]
ALLOWED_TOOLS: Final[frozenset[str]] = frozenset(get_args(MarketToolName))
TOOL_ARGUMENT_KEYS: Final[frozenset[str]] = frozenset({"symbol"})

# The provider codes plus the one application-side code. The ``Literal`` serves
# mypy; ``TOOL_ERROR_CODES`` enforces the same closed set at runtime.
ToolErrorCode = Literal[
    "invalid_input",
    "no_data",
    "rate_limited",
    "authentication_failed",
    "timeout",
    "malformed_provider_response",
    "provider_unavailable",
    "tool_not_allowed",
]
TOOL_ERROR_CODES: Final[frozenset[str]] = PROVIDER_ERROR_CODES | {"tool_not_allowed"}

# The server reports its own deadline first; the client gives up this much later.
CLIENT_TIMEOUT_MARGIN_SECONDS: Final = 2.0

# The stdio entry point, named from the package rather than imported: the
# client reaches the server only across the protocol.
SERVER_MODULE: Final = f"{app.__name__}.mcp_server"
REPOSITORY_ROOT: Final = Path(app.__file__).resolve().parent.parent

# Fixed phrases of the SDK's output-schema RuntimeError (see _runtime_error_code).
_SDK_OUTPUT_VALIDATION_MARKERS: Final = (
    "did not return structured content",
    "Invalid structured content returned by tool",
    "Invalid schema for tool",
)

_MODELS: Final[dict[str, type[MarketQuote | CompanyOverview]]] = {
    "get_market_quote": MarketQuote,
    "get_company_overview": CompanyOverview,
}


@dataclass(frozen=True, slots=True)
class ToolSuccess:
    """One validated tool result."""

    tool: MarketToolName
    result: MarketQuote | CompanyOverview


@dataclass(frozen=True, slots=True)
class ToolFailure:
    """One failed tool request, carrying only a closed code.

    ``tool`` is ``None`` when the requested name was not allowed, so an
    arbitrary name is never kept. Both fields are checked at runtime, with a
    fixed message that never contains the rejected value.
    """

    tool: MarketToolName | None
    error_code: ToolErrorCode

    def __post_init__(self) -> None:
        if self.error_code not in TOOL_ERROR_CODES:
            raise ValueError("unknown tool error code")
        if self.tool is not None and self.tool not in ALLOWED_TOOLS:
            raise ValueError("unknown tool name")


def wire_error_code(
    tool: MarketToolName, content: Sequence[ContentBlock]
) -> ProviderErrorCode:
    """Map an ``is_error`` result's content to a closed code.

    With ``mcp`` 2.2.0 a server ``ToolError(code)`` arrives as the single text
    ``"Error executing tool <tool>: <code>"``. Only that exact text with a
    known code is accepted. Anything else, including no content, a non-text
    first block, another tool's name, or unrecognized text, is
    ``provider_unavailable``. The text itself is never kept.
    """
    if not content or not isinstance(content[0], TextContent):
        return "provider_unavailable"
    prefix = f"Error executing tool {tool}: "
    text = content[0].text
    if text.startswith(prefix) and text[len(prefix) :] in PROVIDER_ERROR_CODES:
        return cast(ProviderErrorCode, text[len(prefix) :])
    return "provider_unavailable"


def interpret_result(
    tool: MarketToolName, symbol: str, result: CallToolResult
) -> ToolSuccess | ToolFailure:
    """Turn a ``tools/call`` result into a success or a closed failure.

    A success needs ``structured_content`` that validates strictly against the
    tool's model (``extra="forbid"``) and carries the requested symbol;
    anything else is ``malformed_provider_response``.
    """
    if result.is_error:
        return ToolFailure(tool, wire_error_code(tool, result.content))
    content = result.structured_content
    model: MarketQuote | CompanyOverview | None = None
    if content is not None:
        try:
            model = _MODELS[tool].model_validate(content)
        except ValidationError:
            model = None
    if model is None or model.symbol != symbol:
        return ToolFailure(tool, "malformed_provider_response")
    return ToolSuccess(tool, model)


class ToolCaller(Protocol):
    """The one ``mcp.Client`` method the tools use."""

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> CallToolResult: ...


class _RequestFailed(Exception):
    """Internal: a request failure already reduced to a closed code.

    It may chain the original exception, but it never leaves this module:
    ``MarketDataTools`` reads only ``code`` and returns a ``ToolFailure``.
    """

    def __init__(self, code: ProviderErrorCode) -> None:
        super().__init__(code)
        self.code: ProviderErrorCode = code


class MarketDataTools:
    """Call the two market-data tools across one open MCP connection."""

    def __init__(self, client: ToolCaller, *, timeout_seconds: float) -> None:
        self._client = client
        self._deadline_seconds = timeout_seconds + CLIENT_TIMEOUT_MARGIN_SECONDS

    async def call(
        self, tool_name: str, arguments: Mapping[str, object]
    ) -> ToolSuccess | ToolFailure:
        """Validate, make at most one ``tools/call`` request, and classify it."""
        started = time.monotonic()
        if tool_name not in ALLOWED_TOOLS:
            return self._failed(None, None, "tool_not_allowed", started)
        tool = cast(MarketToolName, tool_name)
        symbol = _requested_symbol(arguments)
        if symbol is None:
            return self._failed(tool, None, "invalid_input", started)
        _log(logging.INFO, "mcp.tool.requested", tool=tool, symbol=symbol)
        outcome = await self._invoke(tool, symbol)
        if isinstance(outcome, ToolFailure):
            return self._failed(tool, symbol, outcome.error_code, started)
        _log(
            logging.INFO,
            "mcp.tool.completed",
            tool=tool,
            symbol=symbol,
            duration_ms=_elapsed_ms(started),
        )
        return outcome

    async def _invoke(
        self, tool: MarketToolName, symbol: str
    ) -> ToolSuccess | ToolFailure:
        code: ProviderErrorCode
        try:
            result = await self._request(tool, symbol)
        except _RequestFailed as failure:
            code = failure.code
        else:
            return interpret_result(tool, symbol, result)
        return ToolFailure(tool, code)

    async def _request(self, tool: MarketToolName, symbol: str) -> CallToolResult:
        """One ``tools/call``, bounded by the client deadline.

        The arguments are rebuilt as exactly ``{"symbol": <normalized>}``;
        the caller's mapping is never forwarded.
        """
        try:
            with anyio.fail_after(self._deadline_seconds):
                return await self._client.call_tool(
                    tool,
                    {"symbol": symbol},
                    read_timeout_seconds=self._deadline_seconds,
                )
        except TimeoutError as error:
            raise _RequestFailed("timeout") from error
        except MCPError as error:
            code: ProviderErrorCode = (
                "timeout" if error.code == REQUEST_TIMEOUT else "provider_unavailable"
            )
            raise _RequestFailed(code) from error
        except RuntimeError as error:
            raise _RequestFailed(_runtime_error_code(error)) from error
        except Exception as error:
            raise _RequestFailed("provider_unavailable") from error

    def _failed(
        self,
        tool: MarketToolName | None,
        symbol: str | None,
        code: ToolErrorCode,
        started: float,
    ) -> ToolFailure:
        _log(
            logging.WARNING,
            "mcp.tool.failed",
            tool=tool,
            symbol=symbol,
            duration_ms=_elapsed_ms(started),
            error_code=code,
        )
        return ToolFailure(tool, code)


def _runtime_error_code(error: RuntimeError) -> ProviderErrorCode:
    """Tell the SDK's output-schema failure apart from any other RuntimeError.

    ``mcp`` 2.2.0 re-validates ``structured_content`` against the published
    output schema and raises ``RuntimeError`` with one of these fixed phrases
    (``mcp/client/session.py``, ``validate_tool_result``). That is a malformed
    result; any other ``RuntimeError``, such as using a closed client, is not.
    The text is read only to choose a code and is then discarded.
    """
    text = str(error)
    if any(marker in text for marker in _SDK_OUTPUT_VALIDATION_MARKERS):
        return "malformed_provider_response"
    return "provider_unavailable"


def _requested_symbol(arguments: Mapping[str, object]) -> str | None:
    """The normalized symbol, or ``None`` if the arguments are not exact."""
    if not isinstance(arguments, Mapping) or set(arguments) != TOOL_ARGUMENT_KEYS:
        return None
    try:
        return normalize_symbol(arguments["symbol"])
    except InvalidSymbolError:
        return None


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _log(level: int, event: str, **fields: EventField) -> None:
    log_event(logger, event, level=level, provider=PROVIDER, **fields)


@asynccontextmanager
async def open_market_data_tools(
    server: MCPServer[Any] | StdioServerParameters,
    *,
    timeout_seconds: float,
    errlog: TextIO | None = None,
) -> AsyncIterator[MarketDataTools]:
    """Open one MCP connection and close it when the block exits.

    Enter and exit it in the same task: the SDK client holds task groups.
    ``errlog`` applies only to a stdio server: the child's stderr is written
    to that file, which must be a real one since it becomes the child's
    ``stderr``. Without it, the connection opens exactly as before.
    """
    deadline = timeout_seconds + CLIENT_TIMEOUT_MARGIN_SECONDS
    if errlog is not None and isinstance(server, StdioServerParameters):
        client = Client(
            stdio_client(server, errlog=errlog),
            mode="auto",
            cache=None,
            read_timeout_seconds=deadline,
        )
    else:
        client = Client(server, mode="auto", cache=None, read_timeout_seconds=deadline)
    async with client:
        yield MarketDataTools(client, timeout_seconds=timeout_seconds)


def stdio_server_parameters(config: MarketDataConfig) -> StdioServerParameters:
    """Launch parameters for ``python -m <package>.mcp_server``.

    The child inherits only the SDK's allow-listed variables plus these two.
    The result holds the key in ``env``: never log it or its ``repr``.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", SERVER_MODULE],
        cwd=REPOSITORY_ROOT,
        env={
            ALPHA_VANTAGE_API_KEY_ENV: config.api_key,
            MCP_TOOL_TIMEOUT_SECONDS_ENV: str(config.timeout_seconds),
        },
    )
