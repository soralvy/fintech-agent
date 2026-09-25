"""Alpha Vantage market-data adapter (docs/DECISIONS.md section 14).

One provider, two fixed read-only operations: ``GLOBAL_QUOTE`` and
``OVERVIEW`` against one fixed URL. No caller chooses the host, path,
function, or any other query parameter.

Classification fails closed. Alpha Vantage documents neither its response
fields nor its error envelopes (docs/TECH_BASELINE.md section 3.18), so the
shapes handled here are provisional. Only an empty top-level object and an
empty ``"Global Quote"`` object mean ``no_data``; every unrecognized shape is
``malformed_provider_response`` and never becomes data.

Secrets. The API key travels in the query string, which is the only form
the provider accepts, so the URL always carries it. Every failure therefore
leaves this module as ``MarketDataError(<code>)``, raised outside any
``except`` block so it carries neither a cause nor a context; the message is
the closed code alone. Nothing here logs, and ``open_alpha_vantage_provider``
holds the ``httpx`` and ``httpcore`` loggers at ``WARNING`` because httpx logs
every request's full URL at ``INFO`` (docs/TECH_BASELINE.md section 3.17).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated, Final, Literal, Protocol, get_args

import anyio
import httpx
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
)

from app.config import MarketDataConfig
from app.symbols import SYMBOL_PATTERN, InvalidSymbolError, normalize_symbol

ALPHA_VANTAGE_QUERY_URL: Final = "https://www.alphavantage.co/query"
QUOTE_FUNCTION: Final = "GLOBAL_QUOTE"
OVERVIEW_FUNCTION: Final = "OVERVIEW"
PROVIDER: Final = "alpha_vantage"

# docs/SPEC.md section 7.1. The quote endpoint is end-of-day by default.
QUOTE_FRESHNESS: Final = (
    "Provider quote freshness; may be end-of-day depending on entitlement"
)

MAX_PROVIDER_RESPONSE_BYTES: Final = 1024 * 1024
MAX_DESCRIPTION_CHARS: Final = 1000
MAX_SHORT_TEXT_CHARS: Final = 200

# The closed set of codes a provider failure can carry (docs/SPEC.md
# section 7.3). The ``Literal`` serves mypy; the frozenset enforces it at
# runtime and is derived from the same source so the two cannot drift.
ProviderErrorCode = Literal[
    "invalid_input",
    "no_data",
    "rate_limited",
    "authentication_failed",
    "timeout",
    "malformed_provider_response",
    "provider_unavailable",
]
PROVIDER_ERROR_CODES: Final[frozenset[str]] = frozenset(get_args(ProviderErrorCode))

_OVERVIEW_PLACEHOLDERS: Final = frozenset({"", "None", "-"})
_CONTROL_CHARACTERS: Final = re.compile(r"[\x00-\x1f\x7f-\x9f]")

# Provisional error envelopes (docs/changes/M5-mcp-server.md section 11.3).
_ENVELOPE_KEYS: Final = ("Error Message", "Information", "Note")
_RATE_LIMIT_MARKERS: Final = ("rate limit", "call frequency", "requests per")
_KEY_MARKERS: Final = ("apikey", "api key")
_ENTITLEMENT_MARKERS: Final = (*_KEY_MARKERS, "premium")

# Every httpx exception family, plus socket-level errors: the provider could
# not be reached or read. Timeouts are matched first and reported as such.
_UNAVAILABLE_ERRORS: Final = (
    httpx.HTTPError,
    httpx.InvalidURL,
    httpx.CookieConflict,
    httpx.StreamError,
    OSError,
)

_QUOTE_FIELDS: Final = {
    "symbol": "01. symbol",
    "price": "05. price",
    "volume": "06. volume",
    "latest_trading_day": "07. latest trading day",
    "previous_close": "08. previous close",
    "change": "09. change",
    "change_percent": "10. change percent",
}


class MarketDataError(Exception):
    """A provider failure carrying one closed, application-owned code.

    ``str(error)`` is the code and nothing else. An unknown code is a
    programming error and is rejected with a fixed message that never
    contains the rejected value.
    """

    code: ProviderErrorCode

    def __init__(self, code: ProviderErrorCode) -> None:
        if code not in PROVIDER_ERROR_CODES:
            raise ValueError("unknown provider error code")
        super().__init__(code)
        self.code = code


def _iso_date(value: str) -> str:
    """Reject impossible dates such as 2026-02-30; return the string unchanged."""
    date.fromisoformat(value)
    return value


# Financial values stay strings (docs/SPEC.md section 7.1). ``[0-9]`` is
# spelled out because ``\d`` matches any Unicode digit. These are plain
# assignments, not ``type`` statements, so Pydantic inlines each constraint in
# the published output schema instead of emitting ``$defs`` references.
NumericString = Annotated[
    str, StringConstraints(pattern=r"^-?[0-9]+(\.[0-9]+)?$", max_length=32)
]
PercentString = Annotated[
    str, StringConstraints(pattern=r"^-?[0-9]+(\.[0-9]+)?%$", max_length=32)
]
DigitString = Annotated[str, StringConstraints(pattern=r"^[0-9]+$", max_length=20)]
IsoDateString = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    AfterValidator(_iso_date),
]
SymbolString = Annotated[str, StringConstraints(pattern=f"^{SYMBOL_PATTERN.pattern}$")]
ShortText = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_SHORT_TEXT_CHARS)
]
DescriptionText = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_DESCRIPTION_CHARS)
]

_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True, strict=True)


class MarketQuote(BaseModel):
    """The normalized ``get_market_quote`` result (docs/SPEC.md section 7.1)."""

    model_config = _STRICT_MODEL

    provider: Literal["alpha_vantage"]
    symbol: SymbolString
    price: NumericString
    previous_close: NumericString
    change: NumericString
    change_percent: PercentString
    volume: DigitString
    latest_trading_day: IsoDateString
    freshness: Literal[
        "Provider quote freshness; may be end-of-day depending on entitlement"
    ]


class CompanyOverview(BaseModel):
    """The curated ``get_company_overview`` result (docs/SPEC.md section 7.2)."""

    model_config = _STRICT_MODEL

    provider: Literal["alpha_vantage"]
    symbol: SymbolString
    name: ShortText
    description: DescriptionText | None
    exchange: ShortText | None
    currency: ShortText | None
    sector: ShortText | None
    industry: ShortText | None
    market_capitalization: DigitString | None
    latest_quarter: IsoDateString | None


class MarketDataProvider(Protocol):
    """The two provider operations the MCP tools depend on."""

    async def get_quote(self, symbol: str) -> MarketQuote: ...

    async def get_overview(self, symbol: str) -> CompanyOverview: ...


# ---------------------------------------------------------------------------
# Pure classification (docs/changes/M5-mcp-server.md section 11.3)
# ---------------------------------------------------------------------------


def classify_status(status_code: int) -> ProviderErrorCode | None:
    """Return the failure code for a non-200 status, or ``None`` for 200.

    Redirects are not followed, so a 3xx is ``provider_unavailable``.
    """
    if status_code == 200:
        return None
    if status_code == 429:
        return "rate_limited"
    if status_code in (401, 403):
        return "authentication_failed"
    return "provider_unavailable"


def parse_json_object(body: bytes) -> dict[str, object]:
    """Decode a UTF-8 JSON object, or raise ``malformed_provider_response``."""
    parsed: object = None
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        parsed = None
    if not isinstance(parsed, dict):
        raise MarketDataError("malformed_provider_response") from None
    return parsed


def classify_envelope(payload: Mapping[str, object]) -> ProviderErrorCode | None:
    """Return the code for a provider error envelope, or ``None`` if absent.

    The keys are checked in a fixed order and the first present one decides.
    The message is read only to choose a code and is then discarded.
    """
    for key in _ENVELOPE_KEYS:
        if key in payload:
            return _classify_envelope_message(key, payload[key])
    return None


def _classify_envelope_message(key: str, message: object) -> ProviderErrorCode:
    if not isinstance(message, str):
        return "malformed_provider_response"
    text = message.casefold()
    if key == "Error Message":
        # Only a key problem is recognized; any other error is not "no data".
        is_key_error = any(marker in text for marker in _KEY_MARKERS)
        return (
            "authentication_failed" if is_key_error else "malformed_provider_response"
        )
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return "rate_limited"
    if any(marker in text for marker in _ENTITLEMENT_MARKERS):
        return "authentication_failed"
    return "malformed_provider_response"


def classify_response(status_code: int, body: bytes) -> dict[str, object]:
    """Apply steps 2-4: status, JSON object, envelope. Return the payload."""
    status_failure = classify_status(status_code)
    if status_failure is not None:
        raise MarketDataError(status_failure)
    payload = parse_json_object(body)
    envelope_failure = classify_envelope(payload)
    if envelope_failure is not None:
        raise MarketDataError(envelope_failure)
    return payload


def _clean_text(value: str) -> str:
    """Control characters become spaces, whitespace collapses, ends trimmed."""
    return " ".join(_CONTROL_CHARACTERS.sub(" ", value).split())


def _required_quote_value(fields: Mapping[str, object], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, str):
        raise MarketDataError("malformed_provider_response")
    return value.strip()


def _optional_overview_text(payload: Mapping[str, object], key: str) -> str | None:
    if key not in payload:
        return None
    value = payload[key]
    if not isinstance(value, str):
        raise MarketDataError("malformed_provider_response")
    cleaned = _clean_text(value)
    return None if cleaned in _OVERVIEW_PLACEHOLDERS else cleaned


def _build[ModelT: BaseModel](factory: Callable[[], ModelT]) -> ModelT:
    """Build a model; a validation failure is ``malformed_provider_response``.

    The error is raised outside the ``except`` block so the ``ValidationError``,
    whose text echoes provider values, is neither the cause nor the context.
    """
    model: ModelT | None = None
    try:
        model = factory()
    except ValidationError:
        model = None
    if model is None:
        raise MarketDataError("malformed_provider_response")
    return model


def normalize_quote(payload: Mapping[str, object], symbol: str) -> MarketQuote:
    """Step 5 for ``GLOBAL_QUOTE``; ``symbol`` is the requested symbol.

    Only ``{}`` and ``{"Global Quote": {}}`` are ``no_data``. Anything else
    that is not exactly one ``"Global Quote"`` object with every required
    field, the requested symbol, and valid values is malformed.
    """
    if not payload:
        raise MarketDataError("no_data")
    if set(payload) != {"Global Quote"}:
        raise MarketDataError("malformed_provider_response")
    fields = payload["Global Quote"]
    if not isinstance(fields, dict):
        raise MarketDataError("malformed_provider_response")
    if not fields:
        raise MarketDataError("no_data")
    values = {
        name: _required_quote_value(fields, key) for name, key in _QUOTE_FIELDS.items()
    }
    if values["symbol"].upper() != symbol:
        raise MarketDataError("malformed_provider_response")
    return _build(
        lambda: MarketQuote(
            provider=PROVIDER,
            symbol=symbol,
            price=values["price"],
            previous_close=values["previous_close"],
            change=values["change"],
            change_percent=values["change_percent"],
            volume=values["volume"],
            latest_trading_day=values["latest_trading_day"],
            freshness=QUOTE_FRESHNESS,
        )
    )


def normalize_overview(payload: Mapping[str, object], symbol: str) -> CompanyOverview:
    """Step 5 for ``OVERVIEW``; ``symbol`` is the requested symbol.

    Only ``{}`` is ``no_data``. Every returned string has control characters
    replaced and whitespace collapsed; the placeholders ``""``, ``"None"``,
    and ``"-"`` become ``None`` for the optional fields. Only ``description``
    is truncated; any other over-long field is malformed.
    """
    if not payload:
        raise MarketDataError("no_data")
    raw_symbol = payload.get("Symbol")
    raw_name = payload.get("Name")
    if not isinstance(raw_symbol, str) or not isinstance(raw_name, str):
        raise MarketDataError("malformed_provider_response")
    if raw_symbol.strip().upper() != symbol:
        raise MarketDataError("malformed_provider_response")
    name = _clean_text(raw_name)
    if name in _OVERVIEW_PLACEHOLDERS:
        raise MarketDataError("malformed_provider_response")
    description = _optional_overview_text(payload, "Description")
    if description is not None:
        description = description[:MAX_DESCRIPTION_CHARS].rstrip()
    exchange = _optional_overview_text(payload, "Exchange")
    currency = _optional_overview_text(payload, "Currency")
    sector = _optional_overview_text(payload, "Sector")
    industry = _optional_overview_text(payload, "Industry")
    market_capitalization = _optional_overview_text(payload, "MarketCapitalization")
    latest_quarter = _optional_overview_text(payload, "LatestQuarter")
    return _build(
        lambda: CompanyOverview(
            provider=PROVIDER,
            symbol=symbol,
            name=name,
            description=description,
            exchange=exchange,
            currency=currency,
            sector=sector,
            industry=industry,
            market_capitalization=market_capitalization,
            latest_quarter=latest_quarter,
        )
    )


# ---------------------------------------------------------------------------
# HTTP adapter
# ---------------------------------------------------------------------------


def _requested_symbol(symbol: str) -> str:
    """The defensive check immediately before the symbol enters the query."""
    try:
        return normalize_symbol(symbol)
    except InvalidSymbolError:
        pass
    raise MarketDataError("invalid_input")


class AlphaVantageProvider:
    """``MarketDataProvider`` over a shared ``httpx.AsyncClient``.

    The client is owned by the caller (``open_alpha_vantage_provider`` in
    production, a ``MockTransport`` client in tests). There are no retries.
    """

    def __init__(
        self, http_client: httpx.AsyncClient, config: MarketDataConfig
    ) -> None:
        self._client = http_client
        self._config = config

    async def get_quote(self, symbol: str) -> MarketQuote:
        requested = _requested_symbol(symbol)
        payload = await self._query(QUOTE_FUNCTION, requested)
        return normalize_quote(payload, requested)

    async def get_overview(self, symbol: str) -> CompanyOverview:
        requested = _requested_symbol(symbol)
        payload = await self._query(OVERVIEW_FUNCTION, requested)
        return normalize_overview(payload, requested)

    async def _query(
        self, function: Literal["GLOBAL_QUOTE", "OVERVIEW"], symbol: str
    ) -> dict[str, object]:
        status_code, body = await self._fetch(function, symbol)
        return classify_response(status_code, body)

    async def _fetch(self, function: str, symbol: str) -> tuple[int, bytes]:
        """Step 1: one bounded GET. Return the status and, for 200, the body.

        Every expected failure is mapped to a closed code inside the ``except`` blocks
        and raised after them, so no ``httpx`` exception (whose text or
        request carries the keyed URL) is chained to the error.
        """
        params = {
            "function": function,
            "symbol": symbol,
            "apikey": self._config.api_key,
        }
        failure: ProviderErrorCode | None = None
        result: tuple[int, bytes] = (0, b"")
        try:
            with anyio.fail_after(self._config.timeout_seconds):
                result = await self._read(params)
        except MarketDataError as error:
            failure = error.code
        except (TimeoutError, httpx.TimeoutException):
            failure = "timeout"
        except _UNAVAILABLE_ERRORS:
            failure = "provider_unavailable"
        if failure is not None:
            raise MarketDataError(failure)
        return result

    async def _read(self, params: Mapping[str, str]) -> tuple[int, bytes]:
        async with self._client.stream(
            "GET", ALPHA_VANTAGE_QUERY_URL, params=params
        ) as response:
            if response.status_code != 200:
                return response.status_code, b""
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise MarketDataError("malformed_provider_response")
            return response.status_code, bytes(body)


def restrict_http_logging() -> None:
    """Hold ``httpx`` and ``httpcore`` at ``WARNING`` or above.

    httpx logs each request's full URL at ``INFO``, and the Alpha Vantage URL
    carries the API key. A level already at ``WARNING`` or higher is kept.
    """
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if logger.getEffectiveLevel() < logging.WARNING:
            logger.setLevel(logging.WARNING)


@asynccontextmanager
async def open_alpha_vantage_provider(
    config: MarketDataConfig,
) -> AsyncIterator[AlphaVantageProvider]:
    """Yield a provider over a client that is closed when the block exits."""
    restrict_http_logging()
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(config.timeout_seconds),
        headers={"Accept": "application/json"},
    ) as client:
        yield AlphaVantageProvider(client, config)
