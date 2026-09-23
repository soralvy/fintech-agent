"""Configuration parsing: required secrets, fixed dimensions, and safe errors."""

from __future__ import annotations

import pytest

from app import db
from app.config import (
    DEFAULT_MAX_UPLOAD_BYTES,
    PINNED_EMBEDDING_MODEL,
    SCHEMA_EMBEDDING_DIMENSIONS,
    ConfigError,
    IngestionConfig,
    OpenAIConfig,
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
