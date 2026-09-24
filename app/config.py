"""Configuration read from the process environment.

Secrets have no hard-coded defaults and are never rendered back out: a
connection string may embed a password, so it is carried around but never
logged or placed in an error message (docs/SPEC.md section 13).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

DATABASE_URL_ENV = "DATABASE_URL"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_EMBEDDING_MODEL_ENV = "OPENAI_EMBEDDING_MODEL"
OPENAI_LLM_MODEL_ENV = "OPENAI_LLM_MODEL"
OPENAI_EMBEDDING_DIMENSIONS_ENV = "OPENAI_EMBEDDING_DIMENSIONS"
MAX_UPLOAD_BYTES_ENV = "MAX_UPLOAD_BYTES"
RETRIEVAL_TOP_K_ENV = "RETRIEVAL_TOP_K"
MIN_RETRIEVAL_SIMILARITY_ENV = "MIN_RETRIEVAL_SIMILARITY"

DEFAULT_MIN_POOL_SIZE = 1
DEFAULT_MAX_POOL_SIZE = 10

# Upper bound on waiting for a pooled connection. psycopg_pool defaults to 30
# seconds, which turned a database outage into a 30-second stall before
# /health could answer 503 -- longer than a typical health probe will wait.
DEFAULT_POOL_TIMEOUT_SECONDS = 5.0

# The only supported embedding model. Like the dimension below, it is a
# persistence decision: stored vectors are comparable only with vectors from
# the same model, and no model is recorded per row, so a different model would
# silently mix incompatible embedding spaces (docs/TECH_BASELINE.md section
# 3.11). ``OPENAI_EMBEDDING_MODEL`` may be unset or name exactly this model.
PINNED_EMBEDDING_MODEL = "text-embedding-3-small"

# The dimension fixed by ``vector(1536)`` in migrations/001_initial.sql and by
# ``app.db.EMBEDDING_DIMENSIONS``. It is a persistence decision, not a tunable:
# changing it requires a schema migration and re-embedding
# (docs/TECH_BASELINE.md section 3.11).
SCHEMA_EMBEDDING_DIMENSIONS = 1536

# The default answer model (docs/SPEC.md section 14). Unlike the embedding
# model it is not a persistence decision, so ``OPENAI_LLM_MODEL`` may name any
# model, which must support Structured Outputs and ``reasoning.effort="none"``
# (docs/TECH_BASELINE.md section 3.10).
DEFAULT_LLM_MODEL = "gpt-6-luna"

# Bounds each embedding request; the SDK's own retries still apply within it.
DEFAULT_OPENAI_TIMEOUT_SECONDS = 30.0
DEFAULT_OPENAI_MAX_RETRIES = 2

DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_CHUNK_TOKENS = 800
DEFAULT_CHUNK_OVERLAP_TOKENS = 120

# docs/SPEC.md sections 9 and 14. The minimum similarity is a demo heuristic
# for discarding obviously weak chunks, not a calibrated probability.
DEFAULT_RETRIEVAL_TOP_K = 6
DEFAULT_MIN_RETRIEVAL_SIMILARITY = 0.30

# LangSmith arrives transitively through langgraph, and the application never
# enables it (docs/TECH_BASELINE.md section 7). These are every variable that
# LangSmith's ``tracing_is_enabled`` or langchain_core's v1 tracing check
# reads, in the order ``require_tracing_disabled`` reports them.
TRACING_ENV_VARS = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGCHAIN_HANDLER",
)
# The only values both packages treat as disabled, compared exactly as read:
# no trimming and no case folding, since langchain_core's ``env_var_is_set``
# treats "FALSE" or " false " as set and would then fail every graph run.
TRACING_DISABLED_VALUES = frozenset({"", "0", "false", "False"})


class ConfigError(RuntimeError):
    """A required configuration value is missing or unusable."""


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Everything needed to open the connection pool."""

    url: str
    min_pool_size: int = DEFAULT_MIN_POOL_SIZE
    max_pool_size: int = DEFAULT_MAX_POOL_SIZE
    pool_timeout_seconds: float = DEFAULT_POOL_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> DatabaseConfig:
        """Build the database configuration, or raise if it is not set.

        Raises:
            ConfigError: ``DATABASE_URL`` is unset or empty. The message names
                the variable only, never its value.
        """
        url = os.environ.get(DATABASE_URL_ENV, "").strip()
        if not url:
            raise ConfigError(f"{DATABASE_URL_ENV} is not set")
        return cls(url=url)


def require_tracing_disabled() -> None:
    """Refuse to run when any LangSmith tracing variable could enable tracing.

    A protected variable passes only when it is unset or its exact value is in
    ``TRACING_DISABLED_VALUES``. An explicitly set value is refused, never
    silently overridden.

    Raises:
        ConfigError: the first offending variable in ``TRACING_ENV_VARS``
            order. The message names the variable, never its value.
    """
    for name in TRACING_ENV_VARS:
        value = os.environ.get(name)
        if value is not None and value not in TRACING_DISABLED_VALUES:
            raise ConfigError(
                f"{name} must be unset or disabled; LangSmith tracing is not supported"
            )


def _positive_int(name: str, default: int) -> int:
    """Read an optional positive integer, naming the variable (not its value) on error."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a positive integer") from None
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value


def _unit_interval_float(name: str, default: float) -> float:
    """Read an optional finite number in [0, 1], naming the variable (not its value) on error."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    message = f"{name} must be a number between 0 and 1"
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(message) from None
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise ConfigError(message)
    return value


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    """Credentials and model identity for the embedding and answer models.

    ``api_key`` is excluded from ``repr`` so the dataclass can never render it.
    """

    api_key: str = field(repr=False)
    embedding_model: str = PINNED_EMBEDDING_MODEL
    embedding_dimensions: int = SCHEMA_EMBEDDING_DIMENSIONS
    llm_model: str = DEFAULT_LLM_MODEL
    timeout_seconds: float = DEFAULT_OPENAI_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_OPENAI_MAX_RETRIES

    @classmethod
    def from_env(cls) -> OpenAIConfig:
        """Build the provider configuration, or raise if it is unusable.

        ``OPENAI_API_KEY`` is required: embeddings are a mandatory part of the
        current vertical slice, so the application refuses to start without it.

        An unset or blank ``OPENAI_EMBEDDING_MODEL`` resolves to the pinned
        model; any other explicit value is rejected. An unset or blank
        ``OPENAI_LLM_MODEL`` resolves to ``DEFAULT_LLM_MODEL``; any other
        value is kept as an opaque model name.

        Raises:
            ConfigError: the key is unset or empty, the model is not the pinned
                one, or the dimensions do not match the schema. The message
                names the variable, never the supplied value.
        """
        api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
        if not api_key:
            raise ConfigError(f"{OPENAI_API_KEY_ENV} is not set")
        model = (
            os.environ.get(OPENAI_EMBEDDING_MODEL_ENV, "").strip()
            or PINNED_EMBEDDING_MODEL
        )
        if model != PINNED_EMBEDDING_MODEL:
            raise ConfigError(
                f"{OPENAI_EMBEDDING_MODEL_ENV} must be unset or "
                f"{PINNED_EMBEDDING_MODEL}, the model the stored vectors use"
            )
        dimensions = _positive_int(
            OPENAI_EMBEDDING_DIMENSIONS_ENV, SCHEMA_EMBEDDING_DIMENSIONS
        )
        if dimensions != SCHEMA_EMBEDDING_DIMENSIONS:
            raise ConfigError(
                f"{OPENAI_EMBEDDING_DIMENSIONS_ENV} must be "
                f"{SCHEMA_EMBEDDING_DIMENSIONS} to match the vector column"
            )
        llm_model = (
            os.environ.get(OPENAI_LLM_MODEL_ENV, "").strip() or DEFAULT_LLM_MODEL
        )
        return cls(
            api_key=api_key,
            embedding_model=model,
            embedding_dimensions=dimensions,
            llm_model=llm_model,
        )


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    """Upload limit and chunk window (docs/DECISIONS.md sections 7.1 and 7.5)."""

    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    chunk_tokens: int = DEFAULT_CHUNK_TOKENS
    chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0:
            raise ConfigError("max_upload_bytes must be positive")
        if not 0 <= self.chunk_overlap_tokens < self.chunk_tokens:
            raise ConfigError("chunk overlap must be non-negative and below chunk size")

    @classmethod
    def from_env(cls) -> IngestionConfig:
        """Read ``MAX_UPLOAD_BYTES``; the chunk window is fixed by DECISIONS section 7.5."""
        return cls(
            max_upload_bytes=_positive_int(
                MAX_UPLOAD_BYTES_ENV, DEFAULT_MAX_UPLOAD_BYTES
            )
        )


@dataclass(frozen=True, slots=True)
class RetrievalConfig:
    """Candidate count and weak-result threshold (docs/DECISIONS.md section 8).

    Read at startup: the lifespan builds it with the other configuration, so
    an invalid value stops the application before any resource is created.
    """

    top_k: int = DEFAULT_RETRIEVAL_TOP_K
    min_similarity: float = DEFAULT_MIN_RETRIEVAL_SIMILARITY

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ConfigError("top_k must be positive")
        if not (
            math.isfinite(self.min_similarity) and 0.0 <= self.min_similarity <= 1.0
        ):
            raise ConfigError("min_similarity must be a finite number in [0, 1]")

    @classmethod
    def from_env(cls) -> RetrievalConfig:
        """Read ``RETRIEVAL_TOP_K`` and ``MIN_RETRIEVAL_SIMILARITY``.

        Raises:
            ConfigError: a value is malformed or out of range. The message
                names the variable, never the supplied value.
        """
        return cls(
            top_k=_positive_int(RETRIEVAL_TOP_K_ENV, DEFAULT_RETRIEVAL_TOP_K),
            min_similarity=_unit_interval_float(
                MIN_RETRIEVAL_SIMILARITY_ENV, DEFAULT_MIN_RETRIEVAL_SIMILARITY
            ),
        )
