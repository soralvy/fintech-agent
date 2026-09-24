"""Canonical ticker normalization: strip, ASCII, uppercase, full match."""

from __future__ import annotations

import pytest

from app.symbols import SYMBOL_PATTERN, InvalidSymbolError, normalize_symbol


@pytest.mark.parametrize(
    "value", ["MSFT", "BRK.B", "BF-B", "0700.HK", "300135.SHZ", "A", "1"]
)
def test_canonical_symbols_are_returned_unchanged(value: str) -> None:
    assert normalize_symbol(value) == value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("msft", "MSFT"),
        (" msft ", "MSFT"),
        ("\tbrk.b\n", "BRK.B"),
        ("Bf-b", "BF-B"),
        ("  0700.hk  ", "0700.HK"),
    ],
)
def test_case_and_surrounding_whitespace_are_normalized(
    value: str, expected: str
) -> None:
    assert normalize_symbol(value) == expected


@pytest.mark.parametrize("value", [".", "-", ".."])
def test_the_canonical_rule_accepts_punctuation_only_symbols(value: str) -> None:
    # No first-character rule: docs/DECISIONS.md section 14 keeps the regex as is.
    assert normalize_symbol(value) == value


def test_fifteen_characters_are_accepted_and_sixteen_rejected() -> None:
    assert normalize_symbol("A" * 15) == "A" * 15
    assert normalize_symbol(f"  {'a' * 15}  ") == "A" * 15

    with pytest.raises(InvalidSymbolError):
        normalize_symbol("A" * 16)


@pytest.mark.parametrize(
    "value",
    [
        "MS FT",
        "MS\tFT",
        "MSFT\nX",
        "MSFT/X",
        "MSFT_X",
        "MSFT:X",
        "$MSFT",
        "MSFT?apikey=x",
        "https://example.com",
        "../etc",
        "MSFT,AAPL",
    ],
)
def test_invalid_characters_and_embedded_whitespace_are_rejected(value: str) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(value)


def test_a_trailing_newline_is_stripped_but_never_matched_raw() -> None:
    assert normalize_symbol("MSFT\n") == "MSFT"
    # fullmatch, not ``$``: ``re.match(r"...$")`` would accept the raw value.
    assert SYMBOL_PATTERN.fullmatch("MSFT\n") is None


@pytest.mark.parametrize("value", ["", " ", "   ", "\t\n", "　"])
def test_empty_and_whitespace_only_values_are_rejected(value: str) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(value)


@pytest.mark.parametrize(
    "value",
    [
        "ﬁ",  # "ﬁ".upper() == "FI"
        "ß",  # "ß".upper() == "SS"
        "ｍｓｆｔ",  # full-width letters
        "ＭＳＦＴ",
        "MS FT",  # embedded non-breaking space
        "MSFTé",
        "МSFT",  # Cyrillic capital EM
        "١٢٣",  # Arabic-Indic digits
    ],
)
def test_non_ascii_input_is_rejected_before_upper_casing(value: str) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(value)


def test_non_ascii_would_otherwise_become_a_valid_symbol() -> None:
    # Documents why the ASCII check precedes upper-casing.
    assert SYMBOL_PATTERN.fullmatch("ﬁ".upper()) is not None
    assert SYMBOL_PATTERN.fullmatch("ß".upper()) is not None


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, 1, 1.5, b"MSFT", bytearray(b"MSFT"), ["MSFT"], {"MSFT"}],
    ids=lambda value: type(value).__name__,
)
def test_non_string_values_are_rejected(value: object) -> None:
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(value)


@pytest.mark.parametrize(
    "value",
    ["AV-SENTINEL-KEY-7f3a", "sk-live-secret value", "ﬁ", "A" * 16, None, True],
    ids=["sentinel", "secret", "ligature", "too-long", "none", "bool"],
)
def test_the_error_message_is_fixed_and_never_echoes_the_value(value: object) -> None:
    with pytest.raises(InvalidSymbolError) as raised:
        normalize_symbol(value)

    assert str(raised.value) == "invalid symbol"
    assert raised.value.args == ("invalid symbol",)
    assert repr(value) not in repr(raised.value)
    assert raised.value.__cause__ is None


def test_invalid_symbol_error_is_a_value_error() -> None:
    assert issubclass(InvalidSymbolError, ValueError)
