"""Regression test: artifact id parsing must be ASCII-only decimal.

``parse_snowflake_id`` used ``str.isdecimal()`` + ``int()``, which accept
Unicode decimal homoglyphs (Arabic-Indic ``٤٢``, fullwidth ``４₂``,
Devanagari ``४२``). ``int()`` converts each of them to the same value as its
ASCII spelling, so the same bytes can be read as a different id by an
upstream ASCII-only gate or a downstream hop -- a parser differential. The
artifact id arrives as a plain ``str`` path parameter, so this helper is the
only guard. It must reject every non-ASCII digit form and keep the snowflake
range check.
"""

import pytest
from fastapi import HTTPException

from src.api.routes.artifacts.validation import parse_snowflake_id
from src.core.security.hardening import SNOWFLAKE_MAX


def _status(value, field="artifact ID"):
    try:
        return parse_snowflake_id(value, field)
    except HTTPException as exc:
        return exc.status_code


@pytest.mark.parametrize(
    "bad",
    [
        "\u0664\u0662",  # Arabic-Indic digits ٤٢
        "\uff14\uff12",  # fullwidth digits ４２
        "\u0968\u096e",  # Devanagari digits ४२
        "\u06f1\u06f2",  # Extended Arabic-Indic digits ۱۲
        "1_2",  # PEP 515 underscore separator
        "+42",  # leading sign is not an id character
        "-1",  # negative
        "0",  # zero is not a snowflake
        "1⒉",  # mixed ASCII / non-decimal digit
        str(SNOWFLAKE_MAX + 1),  # just past the snowflake envelope
        "9" * 40,  # oversized integer blob
        True,  # bool is not an id
        1.5,  # float fraction must not be truncated
        "",  # empty
    ],
)
def test_homoglyph_and_out_of_range_ids_are_rejected(bad):
    assert _status(bad) == 400


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", 1),
        ("42", 42),
        (" 42 ", 42),  # surrounding whitespace was always tolerated
        (str(SNOWFLAKE_MAX), SNOWFLAKE_MAX),
    ],
)
def test_valid_ascii_ids_still_parse(value, expected):
    assert parse_snowflake_id(value, "artifact ID") == expected


def test_none_is_still_optional():
    assert parse_snowflake_id(None, "artifact ID") is None
