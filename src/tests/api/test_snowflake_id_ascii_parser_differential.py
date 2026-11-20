"""Snowflake string parsing must agree across every layer.

The schema types (``SnowflakeID`` / ``SnowflakeIDParam``) and the
``snowflake_to_str`` / ``snowflake_from_str`` helpers used to parse their
string input with a bare ``int(text)``. ``int`` accepts Unicode decimal
homoglyphs (``١٢٣``, ``１２３``) and PEP 515 separators (``1_2``), while every
route-boundary id guard (``parse_id`` / ``bounded_int``) is ASCII-only. The
same raw token was therefore a valid snowflake to one layer and invalid to
another -- a parser differential in id parsing. These payloads pin that all
layers now reject them identically, while ordinary ASCII forms (including the
whitespace / leading-zero / leading-'+' forms the API has always normalised)
keep working.
"""

import pytest
from pydantic import TypeAdapter, ValidationError

from src.api.schemas.common import (
    SnowflakeID,
    SnowflakeIDParam,
    snowflake_from_str,
    snowflake_to_str,
)
from src.core.security.hardening import parse_id

# Concrete bypass tokens: each is parsed as a valid positive integer by the
# old ``int(text)`` code but is not a plain ASCII decimal id.
HOMOGLYPH_AND_SEPARATOR_IDS = [
    "١٢٣",  # Arabic-Indic digits -> int() == 123
    "１２３",  # fullwidth digits    -> int() == 123
    "٤٢",  # Arabic-Indic 42      -> int() == 42
    "1_2",  # PEP 515 underscore  -> int() == 12
    "+1_2",  # sign + underscore   -> int() == 12
    "2_2",  # underscore inside   -> int() == 22
]


@pytest.mark.parametrize("raw", HOMOGLYPH_AND_SEPARATOR_IDS)
def test_boundary_guard_rejects_payload(raw):
    with pytest.raises(ValueError):
        parse_id(raw)


@pytest.mark.parametrize("raw", HOMOGLYPH_AND_SEPARATOR_IDS)
def test_schema_snowflake_id_rejects_payload(raw):
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeID).validate_python(raw)


@pytest.mark.parametrize("raw", HOMOGLYPH_AND_SEPARATOR_IDS)
def test_schema_snowflake_id_param_rejects_payload(raw):
    with pytest.raises(ValidationError):
        TypeAdapter(SnowflakeIDParam).validate_python(raw)


@pytest.mark.parametrize("raw", HOMOGLYPH_AND_SEPARATOR_IDS)
def test_snowflake_helpers_reject_payload(raw):
    with pytest.raises(ValueError):
        snowflake_to_str(raw)
    with pytest.raises(ValueError):
        snowflake_from_str(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("123", "123"),
        ("  123  ", "123"),
        ("00123", "123"),
        ("+123", "123"),
    ],
)
def test_ascii_noncanonical_forms_still_normalize(raw, expected):
    assert TypeAdapter(SnowflakeID).validate_python(raw) == expected
    assert snowflake_to_str(raw) == expected
