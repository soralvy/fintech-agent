"""Configuration read from the process environment.

Secrets have no hard-coded defaults and are never rendered back out: a
connection string may embed a password, so it is carried around but never
logged or placed in an error message (docs/SPEC.md section 13).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DATABASE_URL_ENV = "DATABASE_URL"
DEFAULT_MIN_POOL_SIZE = 1
DEFAULT_MAX_POOL_SIZE = 10

# Upper bound on waiting for a pooled connection. psycopg_pool defaults to 30
# seconds, which turned a database outage into a 30-second stall before
# /health could answer 503 -- longer than a typical health probe will wait.
DEFAULT_POOL_TIMEOUT_SECONDS = 5.0


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
