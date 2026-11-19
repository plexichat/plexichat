"""Regression: the feature-route id gate enforces the snowflake ceiling.

``src.api.routes.features.common.parse_id`` is the legacy id gate shared by the
slowmode, bookmark, forwarding, scheduled-message, voice, reports and other
feature routes. It used to accept any positive ``str.isdecimal()`` token with
no upper bound and accepted Unicode digit homoglyphs. Consequences:

* an id such as ``99999999999999999999999`` parsed to a >2**63-1 int and was
  bound into SQL queries, where SQLite raises ``OverflowError`` (a validation
  failure degraded to a 500) and into offset arithmetic;
* ``١٢٣`` (Arabic-Indic digits) parsed as ``123``, so the id one hop validated
  could differ from the id the next hop used (parser differential).

It now keeps the strict ASCII-decimal grammar and delegates the range/digit-cap
check to the shared snowflake guard.
"""

import pytest
from fastapi import HTTPException

from src.api.routes.features.common import parse_id
from src.core.security.hardening import SNOWFLAKE_MAX


class TestFeatureParseIdCeiling:
    @pytest.mark.parametrize(
        "value",
        [
            str(SNOWFLAKE_MAX + 1),  # first value past the signed 64-bit ceiling
            str(2**63),
            str(2**70),
            "99999999999999999999999999999999999999",
            "9" * 5000,  # past CPython's int() digit-conversion cap
        ],
    )
    def test_rejects_over_ceiling_ids(self, value):
        with pytest.raises(HTTPException) as exc:
            parse_id(value, "id")
        assert exc.value.status_code == 400
        assert exc.value.detail["error"]["code"] == 400

    @pytest.mark.parametrize(
        "value",
        [
            "١٢٣",  # Arabic-Indic digits
            "１２３",  # fullwidth digits
            "١",  # single homoglyph
            "٠٠١",  # leading homoglyph zeros
        ],
    )
    def test_rejects_unicode_digit_homoglyphs(self, value):
        with pytest.raises(HTTPException) as exc:
            parse_id(value, "id")
        assert exc.value.status_code == 400

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "-1",
            "-0",
            "0",
            "+1",  # the shared bounded_int tolerates '+'; this gate must not
            "1_000",
            "12.5",
            "1e3",
            " 1 2 ",
            "1; DROP TABLE users",
            True,
            False,
            None,
        ],
    )
    def test_rejects_malformed_or_non_positive_values(self, value):
        with pytest.raises(HTTPException):
            parse_id(value, "id")

    @pytest.mark.parametrize(
        "value, expected",
        [
            ("1", 1),
            ("000123", 123),
            (str(SNOWFLAKE_MAX), SNOWFLAKE_MAX),
            (f"  {SNOWFLAKE_MAX}  ", SNOWFLAKE_MAX),
            ("9007199254740993", 9007199254740993),  # > 2**53 JS-safe range
        ],
    )
    def test_accepts_valid_snowflakes(self, value, expected):
        assert parse_id(value, "id") == expected

    def test_field_name_is_preserved_in_error(self):
        with pytest.raises(HTTPException) as exc:
            parse_id(str(2**70), "channel ID")
        assert "channel ID" in exc.value.detail["error"]["message"]
