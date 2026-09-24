"""Alpha Vantage adapter: the complete provider matrix (DECISIONS section 20.4).

Every HTTP exchange goes through ``httpx.MockTransport``; no test opens a
socket or reads a real key. The sentinel key must never appear in an error,
a representation, or a log record.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from pathlib import Path
from typing import Literal, cast

import anyio
import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.config import MarketDataConfig
from app.market_data import (
    ALPHA_VANTAGE_QUERY_URL,
    MAX_PROVIDER_RESPONSE_BYTES,
    PROVIDER_ERROR_CODES,
    QUOTE_FRESHNESS,
    AlphaVantageProvider,
    CompanyOverview,
    MarketDataError,
    MarketDataProvider,
    MarketQuote,
    ProviderErrorCode,
    classify_envelope,
    classify_response,
    classify_status,
    normalize_overview,
    normalize_quote,
    open_alpha_vantage_provider,
    parse_json_object,
    restrict_http_logging,
)
from tests.fakes import alpha_vantage_transport

SENTINEL = "AV-SENTINEL-KEY-7f3a"
FIXTURES = Path(__file__).parent / "fixtures" / "alpha_vantage"

type Respond = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def fixture(name: str) -> dict[str, object]:
    loaded = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return cast(dict[str, object], loaded)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def assert_sanitized(error: MarketDataError, code: ProviderErrorCode) -> None:
    """The error carries the code and nothing else, with no chained exception."""
    assert error.code == code
    assert str(error) == code
    assert error.args == (code,)
    assert error.__cause__ is None
    assert error.__context__ is None
    for rendered in (str(error), repr(error)):
        assert SENTINEL not in rendered
        assert "alphavantage" not in rendered


def replying(status_code: int = 200, body: bytes = b"") -> Respond:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, content=body)

    return respond


def raising(error: Exception) -> Respond:
    async def respond(request: httpx.Request) -> httpx.Response:
        raise error

    return respond


async def call_provider(
    respond: Respond,
    *,
    operation: Literal["quote", "overview"] = "quote",
    symbol: str = "MSFT",
    timeout_seconds: float = 5.0,
) -> tuple[BaseModel | MarketDataError, list[httpx.Request]]:
    recording = alpha_vantage_transport(respond)
    config = MarketDataConfig(api_key=SENTINEL, timeout_seconds=timeout_seconds)
    async with httpx.AsyncClient(
        transport=recording.transport, follow_redirects=False
    ) as client:
        provider: MarketDataProvider = AlphaVantageProvider(client, config)
        result: BaseModel
        try:
            if operation == "quote":
                result = await provider.get_quote(symbol)
            else:
                result = await provider.get_overview(symbol)
        except MarketDataError as error:
            return error, recording.requests
    return result, recording.requests


async def call_failing(
    respond: Respond,
    *,
    operation: Literal["quote", "overview"] = "quote",
    symbol: str = "MSFT",
    timeout_seconds: float = 5.0,
) -> tuple[MarketDataError, list[httpx.Request]]:
    result, requests = await call_provider(
        respond, operation=operation, symbol=symbol, timeout_seconds=timeout_seconds
    )
    assert isinstance(result, MarketDataError)
    return result, requests


@pytest.fixture(autouse=True)
def restore_http_loggers() -> Iterator[None]:
    """``restrict_http_logging`` changes global logger levels; undo it per test."""
    loggers = [logging.getLogger(name) for name in ("httpx", "httpcore")]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


# ---------------------------------------------------------------------------
# Closed error codes (AC19)
# ---------------------------------------------------------------------------


def test_provider_error_codes_are_exactly_the_closed_set() -> None:
    assert PROVIDER_ERROR_CODES == {
        "invalid_input",
        "no_data",
        "rate_limited",
        "authentication_failed",
        "timeout",
        "malformed_provider_response",
        "provider_unavailable",
    }


@pytest.mark.parametrize("code", sorted(PROVIDER_ERROR_CODES))
def test_market_data_error_carries_only_its_code(code: str) -> None:
    error = MarketDataError(cast(ProviderErrorCode, code))

    assert error.code == code
    assert str(error) == code
    assert error.args == (code,)


@pytest.mark.parametrize(
    "code", [SENTINEL, "", "unknown", "RATE_LIMITED", "tool_error"]
)
def test_an_unknown_code_is_rejected_without_echoing_it(code: str) -> None:
    with pytest.raises(ValueError) as raised:
        MarketDataError(cast(ProviderErrorCode, code))

    assert str(raised.value) == "unknown provider error code"


# ---------------------------------------------------------------------------
# Models: strict, frozen, string-valued (AC4)
# ---------------------------------------------------------------------------

VALID_QUOTE: dict[str, object] = {
    "provider": "alpha_vantage",
    "symbol": "MSFT",
    "price": "123.4500",
    "previous_close": "122.1000",
    "change": "-1.3500",
    "change_percent": "-1.1057%",
    "volume": "12345678",
    "latest_trading_day": "2026-09-23",
    "freshness": QUOTE_FRESHNESS,
}

VALID_OVERVIEW: dict[str, object] = {
    "provider": "alpha_vantage",
    "symbol": "MSFT",
    "name": "Example Corp",
    "description": "Makes things.",
    "exchange": "NASDAQ",
    "currency": "USD",
    "sector": "TECHNOLOGY",
    "industry": "SOFTWARE",
    "market_capitalization": "3100000000000",
    "latest_quarter": "2026-06-30",
}


def test_valid_models_keep_every_value_as_the_given_string() -> None:
    quote = MarketQuote.model_validate(VALID_QUOTE)
    overview = CompanyOverview.model_validate(VALID_OVERVIEW)

    assert quote.model_dump() == VALID_QUOTE
    assert overview.model_dump() == VALID_OVERVIEW
    assert quote.price == "123.4500"  # not "123.45": no numeric round trip
    assert type(quote.price) is str
    assert type(overview.market_capitalization) is str


def test_the_freshness_text_is_the_spec_constant() -> None:
    assert QUOTE_FRESHNESS == (
        "Provider quote freshness; may be end-of-day depending on entitlement"
    )
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "freshness": "real-time"})


@pytest.mark.parametrize("model", [MarketQuote, CompanyOverview])
def test_models_reject_extra_fields(model: type[BaseModel]) -> None:
    valid = VALID_QUOTE if model is MarketQuote else VALID_OVERVIEW
    with pytest.raises(ValidationError):
        model.model_validate({**valid, "apikey": SENTINEL})


def test_models_are_frozen() -> None:
    quote = MarketQuote.model_validate(VALID_QUOTE)
    field = "price"
    with pytest.raises(ValidationError):
        setattr(quote, field, "1.00")


@pytest.mark.parametrize(
    "value",
    [
        "1e3",
        "12.",
        ".5",
        "+1",
        "1,000",
        "NaN",
        "inf",
        "",
        " 1.0",
        "１２３",  # full-width digits
        "١٢٣",  # Arabic-Indic digits
        "1" * 33,
        "1.0\n",
    ],
)
def test_numeric_fields_accept_only_ascii_decimal_strings(value: str) -> None:
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "price": value})


@pytest.mark.parametrize("value", [123.45, 123, True, None, b"123.45"])
def test_numeric_fields_are_never_coerced_from_other_types(value: object) -> None:
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "price": value})


@pytest.mark.parametrize("value", ["1.1", "%", "1.1 %", "1.1%%", "１%"])
def test_change_percent_requires_one_trailing_percent_sign(value: str) -> None:
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "change_percent": value})


@pytest.mark.parametrize("value", ["-1", "1.5", "1 000", "1" * 21])
def test_volume_accepts_only_ascii_digits(value: str) -> None:
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "volume": value})


@pytest.mark.parametrize(
    "value",
    ["2026-02-30", "2026-13-01", "2026-9-23", "20260923", "2026-09-23T00:00:00", ""],
)
def test_dates_must_be_real_iso_dates(value: str) -> None:
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "latest_trading_day": value})


@pytest.mark.parametrize("value", ["msft", "MS FT", "A" * 16, ""])
def test_model_symbols_must_be_canonical(value: str) -> None:
    with pytest.raises(ValidationError):
        MarketQuote.model_validate({**VALID_QUOTE, "symbol": value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "x" * 201),
        ("name", ""),
        ("description", "x" * 1001),
        ("sector", "x" * 201),
        ("market_capitalization", "3.1T"),
        ("latest_quarter", "Q2 2026"),
    ],
)
def test_overview_field_constraints(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        CompanyOverview.model_validate({**VALID_OVERVIEW, field: value})


def test_overview_optional_fields_accept_none() -> None:
    optional = [
        "description",
        "exchange",
        "currency",
        "sector",
        "industry",
        "market_capitalization",
        "latest_quarter",
    ]
    overview = CompanyOverview.model_validate(
        {**VALID_OVERVIEW, **dict.fromkeys(optional)}
    )
    assert all(getattr(overview, name) is None for name in optional)


def test_published_schemas_declare_financial_values_as_strings() -> None:
    quote = MarketQuote.model_json_schema()
    overview = CompanyOverview.model_json_schema()

    assert "$defs" not in quote
    assert "$defs" not in overview
    assert quote["additionalProperties"] is False
    for name in ("price", "previous_close", "change", "change_percent", "volume"):
        assert quote["properties"][name]["type"] == "string"
        assert "pattern" in quote["properties"][name]


# ---------------------------------------------------------------------------
# Classification step 2: HTTP status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (200, None),
        (429, "rate_limited"),
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (201, "provider_unavailable"),
        (204, "provider_unavailable"),
        (301, "provider_unavailable"),
        (302, "provider_unavailable"),
        (304, "provider_unavailable"),
        (307, "provider_unavailable"),
        (400, "provider_unavailable"),
        (404, "provider_unavailable"),
        (408, "provider_unavailable"),
        (500, "provider_unavailable"),
        (502, "provider_unavailable"),
        (503, "provider_unavailable"),
        (504, "provider_unavailable"),
    ],
)
def test_classify_status(status_code: int, expected: str | None) -> None:
    assert classify_status(status_code) == expected


# ---------------------------------------------------------------------------
# Classification step 3: UTF-8 JSON object
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"\xff\xfe{}",
        b"<html>Service Unavailable</html>",
        b"[]",
        b'"Global Quote"',
        b"42",
        b"null",
        b"{",
        b"[" * 100_000 + b"]" * 100_000,
    ],
    ids=[
        "empty",
        "not-utf8",
        "html",
        "array",
        "string",
        "number",
        "null",
        "truncated",
        "deeply-nested",
    ],
)
def test_bodies_that_are_not_a_utf8_json_object_are_malformed(body: bytes) -> None:
    with pytest.raises(MarketDataError) as raised:
        parse_json_object(body)

    assert_sanitized(raised.value, "malformed_provider_response")


def test_a_json_object_is_returned_as_a_dict() -> None:
    assert parse_json_object(b'{"Global Quote": {}}') == {"Global Quote": {}}


# ---------------------------------------------------------------------------
# Classification step 4: provider envelopes (provisional shapes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("error_invalid_call", "malformed_provider_response"),
        ("error_invalid_key", "authentication_failed"),
        ("information_rate_limit", "rate_limited"),
        ("note_rate_limit", "rate_limited"),
        ("information_premium", "authentication_failed"),
        ("information_unknown", "malformed_provider_response"),
    ],
)
def test_envelope_fixtures_are_classified(name: str, expected: str) -> None:
    assert classify_envelope(fixture(name)) == expected


def test_the_rate_limit_fixture_also_names_the_key_and_premium() -> None:
    # Proves rate-limit wording is matched before key/premium wording.
    text = cast(str, fixture("information_rate_limit")["Information"]).casefold()
    assert "api key" in text
    assert "premium" in text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"Error Message": "The APIKEY is invalid."}, "authentication_failed"),
        ({"Error Message": "Invalid API Key supplied"}, "authentication_failed"),
        ({"Error Message": ""}, "malformed_provider_response"),
        ({"Information": "RATE LIMIT reached"}, "rate_limited"),
        ({"Note": "Please consider premium"}, "authentication_failed"),
        ({"Note": "Our standard API call frequency is exceeded"}, "rate_limited"),
        ({"Note": "requests per minute exceeded"}, "rate_limited"),
        ({"Information": "Something new"}, "malformed_provider_response"),
        ({"Error Message": None}, "malformed_provider_response"),
        ({"Information": ["rate limit"]}, "malformed_provider_response"),
        ({"Note": {"text": "rate limit"}}, "malformed_provider_response"),
    ],
)
def test_envelope_messages_choose_a_code(
    payload: dict[str, object], expected: str
) -> None:
    assert classify_envelope(payload) == expected


def test_envelope_keys_are_checked_in_a_fixed_order() -> None:
    payload: dict[str, object] = {
        "Note": "call frequency exceeded",
        "Information": "rate limit",
        "Error Message": "apikey invalid",
    }
    assert classify_envelope(payload) == "authentication_failed"
    # "Information" is checked before "Note", whatever the dict order.
    assert (
        classify_envelope({"Note": "premium", "Information": "rate limit"})
        == "rate_limited"
    )


@pytest.mark.parametrize(
    "payload",
    [{}, {"Global Quote": {}}, fixture("global_quote_ok"), fixture("overview_ok")],
)
def test_payloads_without_an_envelope_pass_through(payload: dict[str, object]) -> None:
    assert classify_envelope(payload) is None


def test_an_envelope_wins_over_a_payload() -> None:
    payload = {**fixture("global_quote_ok"), "Information": "rate limit"}
    with pytest.raises(MarketDataError) as raised:
        classify_response(200, json.dumps(payload).encode())

    assert_sanitized(raised.value, "rate_limited")


def test_a_non_200_status_is_classified_before_the_body() -> None:
    with pytest.raises(MarketDataError) as raised:
        classify_response(429, b"not json at all")

    assert_sanitized(raised.value, "rate_limited")


def test_classify_response_returns_the_payload() -> None:
    assert classify_response(200, fixture_bytes("global_quote_ok")) == fixture(
        "global_quote_ok"
    )


# ---------------------------------------------------------------------------
# Classification step 5: quote payload shapes (fail closed)
# ---------------------------------------------------------------------------


def test_a_full_quote_is_normalized_with_strings_unchanged() -> None:
    quote = normalize_quote(fixture("global_quote_ok"), "MSFT")

    assert quote.model_dump() == {
        "provider": "alpha_vantage",
        "symbol": "MSFT",
        "price": "123.4500",
        "previous_close": "122.1000",
        "change": "1.3500",
        "change_percent": "1.1057%",
        "volume": "12345678",
        "latest_trading_day": "2026-09-23",
        "freshness": QUOTE_FRESHNESS,
    }


def test_quote_values_are_stripped_and_the_symbol_is_case_insensitive() -> None:
    fields = cast(dict[str, object], fixture("global_quote_ok")["Global Quote"])
    padded = {key: f"  {value}  " for key, value in fields.items()}
    padded["01. symbol"] = " msft "

    quote = normalize_quote({"Global Quote": padded}, "MSFT")

    assert quote.price == "123.4500"
    assert quote.symbol == "MSFT"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("top_level_empty", "no_data"),
        ("global_quote_empty", "no_data"),
        ("quote_unrecognized_object", "malformed_provider_response"),
        ("global_quote_not_object", "malformed_provider_response"),
    ],
)
def test_quote_shape_fixtures(name: str, expected: ProviderErrorCode) -> None:
    with pytest.raises(MarketDataError) as raised:
        normalize_quote(fixture(name), "MSFT")

    assert_sanitized(raised.value, expected)


@pytest.mark.parametrize(
    "payload",
    [
        {"Global Quote": [], "Meta": 1},
        {"Global Quote": []},
        {"Global Quote": None},
        {"Global Quote": 0},
        {**fixture("global_quote_ok"), "Extra": {}},
        {"global quote": fixture("global_quote_ok")["Global Quote"]},
    ],
    ids=["list-plus-key", "list", "null", "zero", "extra-top-level-key", "wrong-case"],
)
def test_other_quote_shapes_are_malformed_not_no_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises(MarketDataError) as raised:
        normalize_quote(payload, "MSFT")

    assert_sanitized(raised.value, "malformed_provider_response")


@pytest.mark.parametrize(
    "key",
    [
        "01. symbol",
        "05. price",
        "06. volume",
        "07. latest trading day",
        "08. previous close",
        "09. change",
        "10. change percent",
    ],
)
def test_a_missing_or_non_string_quote_field_is_malformed(key: str) -> None:
    fields = dict(cast(dict[str, object], fixture("global_quote_ok")["Global Quote"]))
    for broken in (None, 123.45, ["x"]):
        fields[key] = broken
        with pytest.raises(MarketDataError) as raised:
            normalize_quote({"Global Quote": fields}, "MSFT")
        assert_sanitized(raised.value, "malformed_provider_response")
    del fields[key]
    with pytest.raises(MarketDataError) as raised:
        normalize_quote({"Global Quote": fields}, "MSFT")
    assert_sanitized(raised.value, "malformed_provider_response")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("05. price", "n/a"),
        ("06. volume", "12.5"),
        ("07. latest trading day", "2026-02-30"),
        ("10. change percent", "1.1"),
        ("09. change", SENTINEL),
    ],
)
def test_an_ill_formed_quote_value_is_malformed(key: str, value: str) -> None:
    fields = dict(cast(dict[str, object], fixture("global_quote_ok")["Global Quote"]))
    fields[key] = value

    with pytest.raises(MarketDataError) as raised:
        normalize_quote({"Global Quote": fields}, "MSFT")

    assert_sanitized(raised.value, "malformed_provider_response")


@pytest.mark.parametrize("returned", ["AAPL", "MSFT.X", "", "MSFTX"])
def test_a_quote_for_another_symbol_is_malformed(returned: str) -> None:
    fields = dict(cast(dict[str, object], fixture("global_quote_ok")["Global Quote"]))
    fields["01. symbol"] = returned

    with pytest.raises(MarketDataError) as raised:
        normalize_quote({"Global Quote": fields}, "MSFT")

    assert_sanitized(raised.value, "malformed_provider_response")


# ---------------------------------------------------------------------------
# Classification step 5: overview payload shapes and normalization
# ---------------------------------------------------------------------------


def test_the_overview_keeps_only_curated_fields() -> None:
    overview = normalize_overview(fixture("overview_ok"), "MSFT")

    assert overview.model_dump() == {
        "provider": "alpha_vantage",
        "symbol": "MSFT",
        "name": "Example Corp",
        "description": "Example Corp designs example products and services.",
        "exchange": "NASDAQ",
        "currency": "USD",
        "sector": "TECHNOLOGY",
        "industry": "SERVICES-PREPACKAGED SOFTWARE",
        "market_capitalization": "3100000000000",
        "latest_quarter": "2026-06-30",
    }


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "no_data"),
        (fixture("overview_missing_name"), "malformed_provider_response"),
        ({"Name": "Example Corp"}, "malformed_provider_response"),
        ({"Symbol": "MSFT", "Name": 5}, "malformed_provider_response"),
        ({"Symbol": ["MSFT"], "Name": "Example"}, "malformed_provider_response"),
        ({"Symbol": "AAPL", "Name": "Example"}, "malformed_provider_response"),
        ({"Symbol": "MSFT", "Name": "None"}, "malformed_provider_response"),
        ({"Symbol": "MSFT", "Name": " - "}, "malformed_provider_response"),
        ({"Symbol": "MSFT", "Name": "\x00\t "}, "malformed_provider_response"),
        ({"Unexpected": "shape"}, "malformed_provider_response"),
    ],
)
def test_overview_shapes_fail_closed(
    payload: dict[str, object], expected: ProviderErrorCode
) -> None:
    with pytest.raises(MarketDataError) as raised:
        normalize_overview(payload, "MSFT")

    assert_sanitized(raised.value, expected)


def test_overview_text_replaces_control_characters_and_collapses_whitespace() -> None:
    overview = normalize_overview(
        {
            "Symbol": " msft ",
            "Name": "  Example\x00Corp\t\n  Inc\x85 ",
            "Description": "Line one.\r\nLine\x1b[31m two.\x7f\x9f  ",
            "Sector": " TECHNOLOGY ",
        },
        "MSFT",
    )

    assert overview.name == "Example Corp Inc"
    assert overview.description == "Line one. Line [31m two."
    assert overview.sector == "TECHNOLOGY"


@pytest.mark.parametrize("placeholder", ["", "None", "-", "  None  ", " - ", "\x00"])
def test_overview_placeholders_become_none(placeholder: str) -> None:
    keys = [
        "Description",
        "Exchange",
        "Currency",
        "Sector",
        "Industry",
        "MarketCapitalization",
        "LatestQuarter",
    ]
    payload: dict[str, object] = {"Symbol": "MSFT", "Name": "Example"}
    payload.update(dict.fromkeys(keys, placeholder))

    overview = normalize_overview(payload, "MSFT")

    assert overview.model_dump(exclude={"provider", "symbol", "name"}) == dict.fromkeys(
        [
            "description",
            "exchange",
            "currency",
            "sector",
            "industry",
            "market_capitalization",
            "latest_quarter",
        ]
    )


def test_missing_optional_overview_fields_become_none() -> None:
    overview = normalize_overview({"Symbol": "MSFT", "Name": "Example"}, "MSFT")

    assert overview.description is None
    assert overview.latest_quarter is None


def test_only_the_description_is_truncated() -> None:
    long_text = "word " * 400  # 2000 characters
    overview = normalize_overview(
        {"Symbol": "MSFT", "Name": "Example", "Description": long_text}, "MSFT"
    )

    assert overview.description is not None
    assert len(overview.description) <= 1000
    assert long_text.startswith(overview.description)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("Name", "x" * 201),
        ("Sector", "x" * 201),
        ("Industry", "x" * 201),
        ("Exchange", "x" * 201),
        ("Currency", "x" * 201),
        ("MarketCapitalization", "3.1T"),
        ("MarketCapitalization", "1" * 21),
        ("LatestQuarter", "2026-13-01"),
        ("Sector", 5),
        ("Description", ["text"]),
    ],
)
def test_ill_formed_overview_fields_are_malformed(key: str, value: object) -> None:
    payload: dict[str, object] = {"Symbol": "MSFT", "Name": "Example", key: value}

    with pytest.raises(MarketDataError) as raised:
        normalize_overview(payload, "MSFT")

    assert_sanitized(raised.value, "malformed_provider_response")


# ---------------------------------------------------------------------------
# The HTTP adapter over MockTransport (AC3, AC5, AC6, AC8)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("operation", "function"), [("quote", "GLOBAL_QUOTE"), ("overview", "OVERVIEW")]
)
async def test_the_request_is_fixed(
    operation: Literal["quote", "overview"], function: str
) -> None:
    _, requests = await call_provider(
        replying(body=b"{}"), operation=operation, symbol=" msft "
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    url = request.url
    assert f"{url.scheme}://{url.host}{url.path}" == ALPHA_VANTAGE_QUERY_URL
    # Names and values are contractual; their order is not.
    assert dict(request.url.params) == {
        "function": function,
        "symbol": "MSFT",
        "apikey": SENTINEL,
    }
    assert len(request.url.params.multi_items()) == 3
    assert "datatype" not in request.url.params
    assert "entitlement" not in request.url.params
    assert request.content == b""


@pytest.mark.anyio
async def test_a_successful_quote_and_overview_end_to_end() -> None:
    quote, _ = await call_provider(replying(body=fixture_bytes("global_quote_ok")))
    overview, _ = await call_provider(
        replying(body=fixture_bytes("overview_ok")), operation="overview"
    )

    assert isinstance(quote, MarketQuote)
    assert quote.price == "123.4500"
    assert isinstance(overview, CompanyOverview)
    assert overview.name == "Example Corp"


@pytest.mark.anyio
@pytest.mark.parametrize("symbol", ["BAD SYMBOL", "", "ﬁ", "A" * 16, "../x"])
async def test_an_invalid_symbol_never_reaches_the_provider(symbol: str) -> None:
    error, requests = await call_failing(replying(body=b"{}"), symbol=symbol)

    assert_sanitized(error, "invalid_input")
    assert requests == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (429, "rate_limited"),
        (401, "authentication_failed"),
        (403, "authentication_failed"),
        (204, "provider_unavailable"),
        (404, "provider_unavailable"),
        (500, "provider_unavailable"),
        (503, "provider_unavailable"),
    ],
)
async def test_http_status_classes_over_the_transport(
    status_code: int, expected: ProviderErrorCode
) -> None:
    body = f"upstream said {SENTINEL}".encode()
    error, requests = await call_failing(replying(status_code, body))

    assert_sanitized(error, expected)
    assert len(requests) == 1  # no retries


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
async def test_redirects_are_not_followed(status_code: int) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code, headers={"Location": "https://attacker.invalid/query"}
        )

    error, requests = await call_failing(respond)

    assert_sanitized(error, "provider_unavailable")
    assert len(requests) == 1
    assert requests[0].url.host == "www.alphavantage.co"


@pytest.mark.anyio
async def test_a_body_at_the_cap_is_read() -> None:
    body = b'{"Global Quote": {}}'
    body += b" " * (MAX_PROVIDER_RESPONSE_BYTES - len(body))
    assert len(body) == MAX_PROVIDER_RESPONSE_BYTES

    error, _ = await call_failing(replying(body=body))

    assert_sanitized(error, "no_data")


@pytest.mark.anyio
async def test_a_body_over_the_cap_is_malformed() -> None:
    body = b'{"Global Quote": {}}'
    body += b" " * (MAX_PROVIDER_RESPONSE_BYTES + 1 - len(body))

    error, _ = await call_failing(replying(body=body))

    assert_sanitized(error, "malformed_provider_response")


@pytest.mark.anyio
async def test_reading_stops_at_the_cap() -> None:
    chunk = b" " * 65_536
    yielded = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal yielded
        while True:
            yielded += 1
            yield chunk

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless())

    error, _ = await call_failing(respond)

    assert_sanitized(error, "malformed_provider_response")
    assert yielded <= MAX_PROVIDER_RESPONSE_BYTES // len(chunk) + 2


@pytest.mark.anyio
async def test_a_stalled_provider_times_out_within_the_deadline() -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(10)
        return httpx.Response(200, content=b"{}")

    started = time.monotonic()
    error, requests = await call_failing(respond, timeout_seconds=0.05)

    assert_sanitized(error, "timeout")
    assert time.monotonic() - started < 2.0
    assert len(requests) == 1


@pytest.mark.anyio
async def test_a_stalled_body_times_out_within_the_deadline() -> None:
    async def slow() -> AsyncIterator[bytes]:
        yield b'{"Global '
        await anyio.sleep(10)
        yield b'Quote": {}}'

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow())

    error, _ = await call_failing(respond, timeout_seconds=0.05)

    assert_sanitized(error, "timeout")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ReadTimeout(f"read timed out {SENTINEL}"), "timeout"),
        (httpx.ConnectTimeout("connect timed out"), "timeout"),
        (httpx.PoolTimeout("pool timed out"), "timeout"),
        (httpx.ConnectError(f"connection refused {SENTINEL}"), "provider_unavailable"),
        (httpx.ReadError("connection reset"), "provider_unavailable"),
        (httpx.RemoteProtocolError("bad framing"), "provider_unavailable"),
        (httpx.ProxyError("proxy refused"), "provider_unavailable"),
        (httpx.UnsupportedProtocol("no"), "provider_unavailable"),
        (httpx.StreamClosed(), "provider_unavailable"),
        (OSError(f"network unreachable {SENTINEL}"), "provider_unavailable"),
        (ConnectionResetError("reset"), "provider_unavailable"),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else value,
)
async def test_transport_failures_are_classified_and_sanitized(
    error: Exception, expected: ProviderErrorCode
) -> None:
    raised, requests = await call_failing(raising(error))

    assert_sanitized(raised, expected)
    assert len(requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "operation", "expected"),
    [
        ("information_rate_limit", "quote", "rate_limited"),
        ("error_invalid_key", "overview", "authentication_failed"),
        ("error_invalid_call", "quote", "malformed_provider_response"),
        ("global_quote_empty", "quote", "no_data"),
        ("top_level_empty", "overview", "no_data"),
        ("quote_unrecognized_object", "quote", "malformed_provider_response"),
    ],
)
async def test_fixtures_classify_end_to_end(
    name: str, operation: Literal["quote", "overview"], expected: ProviderErrorCode
) -> None:
    error, requests = await call_failing(
        replying(body=fixture_bytes(name)), operation=operation
    )

    assert_sanitized(error, expected)
    assert len(requests) == 1


@pytest.mark.anyio
async def test_a_non_json_200_is_malformed() -> None:
    error, _ = await call_failing(replying(body=b"<html>maintenance</html>"))

    assert_sanitized(error, "malformed_provider_response")


# ---------------------------------------------------------------------------
# Logging and lifecycle (AC7)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unrestricted_httpx_would_log_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Control: without the restriction httpx writes the keyed URL at INFO, so
    # the next test's absence check is meaningful.
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    caplog.set_level(logging.DEBUG)

    await call_provider(replying(body=b"{}"))

    assert any(SENTINEL in record.getMessage() for record in caplog.records)


@pytest.mark.anyio
async def test_no_log_record_contains_the_key_after_restriction(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    logging.getLogger("httpcore").setLevel(logging.NOTSET)
    caplog.set_level(logging.DEBUG)
    restrict_http_logging()

    await call_provider(replying(body=fixture_bytes("global_quote_ok")))
    await call_provider(replying(429, SENTINEL.encode()))
    await call_provider(raising(httpx.ConnectError(SENTINEL)))
    await call_provider(replying(body=fixture_bytes("information_rate_limit")))

    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert "apikey" not in record.getMessage()
        assert not record.name.startswith("httpx")
        assert not record.name.startswith("app")


@pytest.mark.parametrize("name", ["httpx", "httpcore"])
def test_restriction_raises_low_levels_and_keeps_higher_ones(name: str) -> None:
    logger = logging.getLogger(name)

    logger.setLevel(logging.DEBUG)
    restrict_http_logging()
    assert logger.getEffectiveLevel() == logging.WARNING

    logger.setLevel(logging.ERROR)
    restrict_http_logging()
    assert logger.level == logging.ERROR


@pytest.mark.anyio
async def test_open_provider_configures_and_closes_its_client() -> None:
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.DEBUG)
    config = MarketDataConfig(api_key=SENTINEL, timeout_seconds=7.5)

    async with open_alpha_vantage_provider(config) as provider:
        client = provider._client
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
        assert client.follow_redirects is False
        assert client.timeout == httpx.Timeout(7.5)
        assert client.headers["Accept"] == "application/json"
        assert not client.is_closed

    assert client.is_closed
