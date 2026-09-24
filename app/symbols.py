"""Canonical ticker-symbol normalization (docs/DECISIONS.md section 14).

One rule for every boundary that accepts a symbol: the MCP tools, the
application-side MCP client, and, from Milestone 6, the planner check. The
module is pure and imports only the standard library, so the graph can use
it without importing an HTTP or MCP package.

The error message is fixed and never contains the rejected value, so a
symbol taken from a question or a model output cannot reach a log or a
client through an exception.
"""

from __future__ import annotations

import re
from typing import Final

# The canonical pattern of docs/SPEC.md section 7.1. It is applied with
# ``re.fullmatch``: ``$`` would also match before a trailing newline.
SYMBOL_PATTERN: Final = re.compile(r"[A-Z0-9.-]{1,15}")


class InvalidSymbolError(ValueError):
    """The value is not a valid ticker symbol."""

    def __init__(self) -> None:
        super().__init__("invalid symbol")


def normalize_symbol(value: object) -> str:
    """Return the canonical form of ``value``, or raise ``InvalidSymbolError``.

    The value is stripped, checked to be ASCII, upper-cased, and matched in
    full against ``SYMBOL_PATTERN``. ASCII is checked before upper-casing
    because ``str.upper`` maps some non-ASCII characters to ASCII letters
    (``"ﬁ"`` becomes ``"FI"``, ``"ß"`` becomes ``"SS"``), which would
    otherwise turn them into a valid symbol.
    """
    if not isinstance(value, str):
        raise InvalidSymbolError
    stripped = value.strip()
    if not stripped.isascii():
        raise InvalidSymbolError
    normalized = stripped.upper()
    if SYMBOL_PATTERN.fullmatch(normalized) is None:
        raise InvalidSymbolError
    return normalized
