"""Configuration parsing: required secrets, fixed dimensions, and safe errors."""

from __future__ import annotations

import math
import os

import pytest

from app import db
from app.config import (
    DEFAULT_LLM_MODEL,
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_MCP_TOOL_TIMEOUT_SECONDS,
    PINNED_EMBEDDING_MODEL,
    SCHEMA_EMBEDDING_DIMENSIONS,
    TRACING_DISABLED_VALUES,
    TRACING_ENV_VARS,
    ConfigError,
    IngestionConfig,
    MarketDataConfig,
    OpenAIConfig,
    RetrievalConfig,
    require_tracing_disabled,
)

SECRET = "sk-live-must-never-be-rendered"


def test_openai_config_reads_the_key_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", f"  {SECRET}  ")
    monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_EMBEDDING_DIMENSIONS", raising=False)

    config = OpenAIConfig.from_env()

    assert config.api_key == SECRET
    assert config.embedding_model == "text-embedding-3-small"
    assert config.embedding_dimensions == 1536
    assert SECRET not in repr(config)


@pytest.mark.parametrize("value", ["", "   ", "text-embedding-3-small"])
def test_blank_or_pinned_embedding_model_resolves_to_the_pin(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", value)

    assert OpenAIConfig.from_env().embedding_model == PINNED_EMBEDDING_MODEL
    assert PINNED_EMBEDDING_MODEL == "text-embedding-3-small"


@pytest.mark.parametrize(
    "value",
    ["text-embedding-3-large", "text-embedding-ada-002", "Text-Embedding-3-Small"],
)
def test_any_other_embedding_model_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """3-large accepts ``dimensions=1536``, so only the model check stops it
    from writing vectors in a different embedding space."""
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", value)

    with pytest.raises(ConfigError) as raised:
        OpenAIConfig.from_env()

    message = str(raised.value)
    assert message.startswith("OPENAI_EMBEDDING_MODEL must be unset or ")
    assert value not in message.removeprefix(
        "OPENAI_EMBEDDING_MODEL must be unset or text-embedding-3-small"
    )
    assert SECRET not in message


@pytest.mark.parametrize("value", ["768", "3072"])
def test_embedding_dimensions_must_match_the_schema(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_EMBEDDING_DIMENSIONS", value)

    with pytest.raises(ConfigError) as raised:
        OpenAIConfig.from_env()

    assert "OPENAI_EMBEDDING_DIMENSIONS" in str(raised.value)
    assert SECRET not in str(raised.value)


def test_schema_dimension_constants_agree() -> None:
    assert SCHEMA_EMBEDDING_DIMENSIONS == db.EMBEDDING_DIMENSIONS


def test_upload_limit_defaults_to_10_mib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)

    config = IngestionConfig.from_env()

    assert config.max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES == 10_485_760
    assert (config.chunk_tokens, config.chunk_overlap_tokens) == (800, 120)


@pytest.mark.parametrize("value", ["abc", "0", "-5", "1.5"])
def test_invalid_upload_limit_names_the_setting_not_the_value(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", value)

    with pytest.raises(ConfigError) as raised:
        IngestionConfig.from_env()

    assert str(raised.value) == "MAX_UPLOAD_BYTES must be a positive integer"


def test_chunk_overlap_must_be_smaller_than_the_chunk() -> None:
    with pytest.raises(ConfigError):
        IngestionConfig(chunk_tokens=100, chunk_overlap_tokens=100)


# ---------------------------------------------------------------------------
# RetrievalConfig
# ---------------------------------------------------------------------------


def test_retrieval_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRIEVAL_TOP_K", raising=False)
    monkeypatch.delenv("MIN_RETRIEVAL_SIMILARITY", raising=False)

    config = RetrievalConfig.from_env()

    assert (config.top_k, config.min_similarity) == (6, 0.30)


def test_retrieval_config_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RETRIEVAL_TOP_K", " 3 ")
    monkeypatch.setenv("MIN_RETRIEVAL_SIMILARITY", "0")

    config = RetrievalConfig.from_env()

    assert (config.top_k, config.min_similarity) == (3, 0.0)


@pytest.mark.parametrize("value", ["0", "-1", "2.5", "six"])
def test_invalid_top_k_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RETRIEVAL_TOP_K", value)

    with pytest.raises(ConfigError, match="RETRIEVAL_TOP_K") as raised:
        RetrievalConfig.from_env()

    assert str(raised.value) == "RETRIEVAL_TOP_K must be a positive integer"


@pytest.mark.parametrize(
    "value", ["nan", "inf", "-inf", "-0.01", "1.01", "high", "0x1p-2"]
)
def test_invalid_minimum_similarity_is_rejected_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("MIN_RETRIEVAL_SIMILARITY", value)

    with pytest.raises(ConfigError, match="MIN_RETRIEVAL_SIMILARITY") as raised:
        RetrievalConfig.from_env()

    assert (
        str(raised.value) == "MIN_RETRIEVAL_SIMILARITY must be a number between 0 and 1"
    )


@pytest.mark.parametrize(
    ("top_k", "min_similarity"), [(0, 0.3), (6, math.nan), (6, -0.1), (6, 1.5)]
)
def test_retrieval_config_rejects_invalid_direct_values(
    top_k: int, min_similarity: float
) -> None:
    with pytest.raises(ConfigError, match="must be"):
        RetrievalConfig(top_k=top_k, min_similarity=min_similarity)


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_unset_or_blank_llm_model_resolves_to_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    if value is None:
        monkeypatch.delenv("OPENAI_LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("OPENAI_LLM_MODEL", value)

    assert OpenAIConfig.from_env().llm_model == DEFAULT_LLM_MODEL
    assert DEFAULT_LLM_MODEL == "gpt-6-luna"


def test_an_explicit_llm_model_is_kept_as_an_opaque_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_LLM_MODEL", "  some-other-model-2026  ")

    config = OpenAIConfig.from_env()

    assert config.llm_model == "some-other-model-2026"
    assert config.embedding_model == PINNED_EMBEDDING_MODEL


# ---------------------------------------------------------------------------
# LangSmith tracing refusal (AC14; docs/TECH_BASELINE.md section 7)
# ---------------------------------------------------------------------------

REJECTED_TRACING_VALUES = ["true", "1", "FALSE", "   ", " false ", "yes-sentinel-7f3"]
ACCEPTED_TRACING_VALUES = ["", "0", "false", "False"]


def tracing_error(name: str) -> str:
    return f"{name} must be unset or disabled; LangSmith tracing is not supported"


def test_the_protected_tracing_set_is_exact() -> None:
    assert TRACING_ENV_VARS == (
        "LANGSMITH_TRACING",
        "LANGSMITH_TRACING_V2",
        "LANGCHAIN_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGCHAIN_HANDLER",
    )
    assert TRACING_DISABLED_VALUES == {"", "0", "false", "False"}


def test_tracing_variables_are_cleared_for_every_test() -> None:
    """``tests/conftest.py`` removes them at import and again per test."""
    assert not [name for name in TRACING_ENV_VARS if name in os.environ]


@pytest.mark.parametrize("value", REJECTED_TRACING_VALUES)
@pytest.mark.parametrize("name", TRACING_ENV_VARS)
def test_each_tracing_variable_alone_refuses_a_non_disabled_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError) as caught:
        require_tracing_disabled()

    assert str(caught.value) == tracing_error(name)


def test_a_non_empty_langchain_handler_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_HANDLER", "langchain")

    with pytest.raises(ConfigError) as caught:
        require_tracing_disabled()

    assert str(caught.value) == tracing_error("LANGCHAIN_HANDLER")


def test_the_first_offender_in_order_is_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_HANDLER", "true")
    monkeypatch.setenv("LANGSMITH_TRACING_V2", "1")

    with pytest.raises(ConfigError) as caught:
        require_tracing_disabled()

    assert str(caught.value) == tracing_error("LANGSMITH_TRACING_V2")


def test_all_tracing_variables_unset_is_accepted() -> None:
    require_tracing_disabled()


@pytest.mark.parametrize("value", ACCEPTED_TRACING_VALUES)
@pytest.mark.parametrize("name", TRACING_ENV_VARS)
def test_each_exact_disabled_value_is_accepted(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    require_tracing_disabled()


# ---------------------------------------------------------------------------
# MarketDataConfig
# ---------------------------------------------------------------------------

AV_SECRET = "AV-SENTINEL-KEY-7f3a"
TIMEOUT_ERROR = (
    "MCP_TOOL_TIMEOUT_SECONDS must be a number greater than 0 and at most 30"
)


def test_market_data_config_reads_and_trims_the_key_with_the_default_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", f"  {AV_SECRET}\n")
    monkeypatch.delenv("MCP_TOOL_TIMEOUT_SECONDS", raising=False)

    config = MarketDataConfig.from_env()

    assert config.api_key == AV_SECRET
    assert config.timeout_seconds == 5.0
    assert DEFAULT_MCP_TOOL_TIMEOUT_SECONDS == 5.0


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_market_data_key_is_required(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", value)

    with pytest.raises(ConfigError) as raised:
        MarketDataConfig.from_env()

    assert str(raised.value) == "ALPHA_VANTAGE_API_KEY is not set"


def test_market_data_repr_never_renders_the_key() -> None:
    config = MarketDataConfig(api_key=AV_SECRET, timeout_seconds=7.5)

    assert AV_SECRET not in repr(config)
    assert AV_SECRET not in str(config)
    assert "timeout_seconds=7.5" in repr(config)


@pytest.mark.parametrize("value", ["", "   "], ids=["empty", "blank"])
def test_blank_timeout_resolves_to_the_default(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SECRET)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", value)

    assert MarketDataConfig.from_env().timeout_seconds == 5.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30", 30.0), ("30.0", 30.0), (" 2.5 ", 2.5), ("0.001", 0.001), ("1e1", 10.0)],
)
def test_valid_timeouts_are_read(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: float
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SECRET)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", value)

    assert MarketDataConfig.from_env().timeout_seconds == expected


INVALID_TIMEOUTS = [
    "0",
    "0.0",
    "-0",
    "-1",
    "-0.5",
    "30.0001",
    "31",
    "1e9",
    "nan",
    "NaN",
    "inf",
    "-inf",
    "Infinity",
    "five",
    "5s",
    "5,0",
    "0x5",
    "AV-SENTINEL-KEY-7f3a",
]


@pytest.mark.parametrize("value", INVALID_TIMEOUTS)
def test_invalid_timeouts_are_rejected_without_echoing_them(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SECRET)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigError) as raised:
        MarketDataConfig.from_env()

    # An exact fixed message proves the rejected value is not echoed.
    assert str(raised.value) == TIMEOUT_ERROR
    assert AV_SECRET not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize(
    "timeout",
    [0.0, -1.0, 30.0001, 31.0, math.nan, math.inf, -math.inf],
)
def test_direct_construction_enforces_the_same_timeout_bounds(timeout: float) -> None:
    with pytest.raises(ConfigError) as raised:
        MarketDataConfig(api_key=AV_SECRET, timeout_seconds=timeout)

    assert str(raised.value) == TIMEOUT_ERROR


@pytest.mark.parametrize("timeout", [0.001, 5.0, 30.0])
def test_direct_construction_accepts_the_valid_range(timeout: float) -> None:
    assert (
        MarketDataConfig(api_key=AV_SECRET, timeout_seconds=timeout).timeout_seconds
        == timeout
    )


def test_the_key_error_is_checked_before_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", "nan")

    with pytest.raises(ConfigError) as raised:
        MarketDataConfig.from_env()

    assert str(raised.value) == "ALPHA_VANTAGE_API_KEY is not set"


# --- The API's optional configuration (M6 D2, T25) ---------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\t\n"])
def test_the_optional_config_is_none_without_a_key(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.delenv("MCP_TOOL_TIMEOUT_SECONDS", raising=False)
    if value is None:
        monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", value)

    assert MarketDataConfig.optional_from_env() is None


def test_the_optional_config_reads_and_trims_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", f"  {AV_SECRET}\n")
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", " 7.5 ")

    config = MarketDataConfig.optional_from_env()

    assert config == MarketDataConfig(api_key=AV_SECRET, timeout_seconds=7.5)
    assert AV_SECRET not in repr(config)


@pytest.mark.parametrize("value", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_the_optional_config_defaults_the_timeout(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", AV_SECRET)
    if value is None:
        monkeypatch.delenv("MCP_TOOL_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", value)

    config = MarketDataConfig.optional_from_env()

    assert config is not None
    assert config.timeout_seconds == DEFAULT_MCP_TOOL_TIMEOUT_SECONDS


@pytest.mark.parametrize("key", [None, AV_SECRET], ids=["without-key", "with-key"])
@pytest.mark.parametrize("value", INVALID_TIMEOUTS)
def test_the_optional_config_always_validates_the_timeout(
    monkeypatch: pytest.MonkeyPatch, key: str | None, value: str
) -> None:
    if key is None:
        monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", key)
    monkeypatch.setenv("MCP_TOOL_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigError) as raised:
        MarketDataConfig.optional_from_env()

    assert str(raised.value) == TIMEOUT_ERROR
    assert AV_SECRET not in str(raised.value)
    assert raised.value.__cause__ is None
