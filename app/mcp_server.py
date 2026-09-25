"""The local, read-only market-data MCP server (docs/DECISIONS.md section 3.5).

Exactly two tools, ``get_market_quote`` and ``get_company_overview``, each
taking only ``symbol``. Both call one ``MarketDataProvider``.

Every tool body is a sanitizing boundary. The symbol is validated with the
canonical rule before the provider is called, and every failure leaves the
tool as ``ToolError(<code>)`` with a closed code and no chained exception.
The MCP SDK sends that text as ``"Error executing tool <tool>: <code>"``,
and a ``ToolError`` is logged without a traceback, so no provider exception
or message reaches the wire or a log (docs/DECISIONS.md section 14).
Cancellation is never caught.

Run over stdio as ``python -m app.mcp_server``. Importing this module has no
side effects: nothing is configured or constructed until ``main`` runs.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any, Final

import anyio
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from app.config import ConfigError, MarketDataConfig
from app.market_data import (
    CompanyOverview,
    MarketDataError,
    MarketDataProvider,
    MarketQuote,
    ProviderErrorCode,
    open_alpha_vantage_provider,
    restrict_http_logging,
)
from app.symbols import InvalidSymbolError, normalize_symbol

SERVER_NAME: Final = "fintech-market-data"
SERVER_VERSION: Final = "0.1.0"
TOOL_NAMES: Final = ("get_market_quote", "get_company_overview")

QUOTE_DESCRIPTION: Final = (
    "Latest quote for one equity ticker symbol from Alpha Vantage. Read-only. "
    "Provider data, not real-time: it may be end-of-day depending on entitlement."
)
OVERVIEW_DESCRIPTION: Final = (
    "Curated company overview for one equity ticker symbol from Alpha Vantage. "
    "Read-only. Provider data, not real-time: it may be end-of-day and is "
    "refreshed when the company reports earnings."
)

# Hints only; the security boundary is the implementation (DECISIONS section 18).
TOOL_ANNOTATIONS: Final = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)

LOG_FORMAT: Final = "%(levelname)s %(name)s %(message)s"
CONFIG_ERROR_EXIT_CODE: Final = 2


async def _call_provider[ModelT: BaseModel](
    operation: Callable[[str], Awaitable[ModelT]], symbol: str
) -> ModelT:
    """Validate ``symbol`` and call the provider, in the adapter's vocabulary.

    Raises ``InvalidSymbolError`` or ``MarketDataError``. Any other exception
    from the provider becomes ``MarketDataError("provider_unavailable")``; it
    stays chained only here, and ``_run_tool`` drops it.
    """
    normalized = normalize_symbol(symbol)
    try:
        return await operation(normalized)
    except MarketDataError:
        raise
    except Exception as unexpected:
        raise MarketDataError("provider_unavailable") from unexpected


async def _run_tool[ModelT: BaseModel](
    operation: Callable[[str], Awaitable[ModelT]], symbol: str
) -> ModelT:
    """The sanitizing tool boundary: a result, or ``ToolError(<closed code>)``.

    The ``ToolError`` is raised after the ``except`` blocks, so it has neither
    a cause nor a context and carries no provider exception or message.
    """
    code: ProviderErrorCode
    try:
        return await _call_provider(operation, symbol)
    except InvalidSymbolError:
        code = "invalid_input"
    except MarketDataError as error:
        code = error.code
    raise ToolError(code)


def build_mcp_server(provider: MarketDataProvider) -> MCPServer[Any]:
    """Register exactly the two approved tools. Pure construction, no I/O."""
    server: MCPServer[Any] = MCPServer(
        SERVER_NAME, version=SERVER_VERSION, log_level="WARNING"
    )

    @server.tool(
        name="get_market_quote",
        description=QUOTE_DESCRIPTION,
        annotations=TOOL_ANNOTATIONS,
        structured_output=True,
    )
    async def get_market_quote(symbol: str) -> MarketQuote:
        return await _run_tool(provider.get_quote, symbol)

    @server.tool(
        name="get_company_overview",
        description=OVERVIEW_DESCRIPTION,
        annotations=TOOL_ANNOTATIONS,
        structured_output=True,
    )
    async def get_company_overview(symbol: str) -> CompanyOverview:
        return await _run_tool(provider.get_overview, symbol)

    return server


def configure_server_logging() -> None:
    """Log to stderr at ``WARNING``, before any ``MCPServer`` is constructed.

    The SDK's own ``basicConfig`` then finds a handler and does nothing, its
    per-failure ``INFO`` lines are suppressed, and ``httpx``/``httpcore`` are
    held at ``WARNING`` because httpx logs the keyed request URL at ``INFO``.
    """
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format=LOG_FORMAT)
    restrict_http_logging()


async def serve(config: MarketDataConfig) -> None:
    """Serve both tools over stdio until the client closes the connection."""
    async with open_alpha_vantage_provider(config) as provider:
        await build_mcp_server(provider).run_stdio_async()


def main() -> None:
    """Entry point for ``python -m app.mcp_server``.

    A configuration error writes one line naming the variable, never its
    value, and exits with status 2.
    """
    configure_server_logging()
    message: str
    try:
        config = MarketDataConfig.from_env()
    except ConfigError as error:
        message = str(error)
    else:
        anyio.run(serve, config)
        return
    sys.stderr.write(f"{SERVER_NAME}: {message}\n")
    raise SystemExit(CONFIG_ERROR_EXIT_CODE)


if __name__ == "__main__":
    main()
