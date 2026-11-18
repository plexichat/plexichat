"""Regression tests for two unbounded search-query inputs that used to 500.

1. A control character (notably NUL) in the query survived ``validate_search_query``
   and reached the SQLite FTS5 MATCH expression, where sqlite3 raises
   ``OperationalError: unterminated string``. An authenticated caller could turn
   every search endpoint into a 500 with ``?query=%00``. The shared
   ``reject_control_characters`` guard now rejects the whole class at the
   boundary with a 400-class ``InvalidQuerySyntaxError``.

2. Relative date filters (``after:7d``) accepted an arbitrarily long digit run.
   ``int()`` parsed it but ``timedelta(days=huge)`` raises ``OverflowError`` out
   of ``QueryParser.parse``, again a 500. The resolved span is now clamped to a
   century so it stays inside datetime's range.

Both are exercised through the real parser/validator, not mocks.
"""

from __future__ import annotations

import pytest

from src.core.search.exceptions import InvalidQuerySyntaxError
from src.core.search.manager.message_search import validate_search_query
from src.core.search.models import FilterType
from src.core.search.query.parser import QueryParser, MAX_RELATIVE_DATE_DAYS

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.mark.parametrize(
    "dirty",
    [
        "foo\x00bar",  # NUL -> FTS OperationalError before the fix
        "foo\r\nbar",  # CR/LF -> log/frame forging
        "foo\x1b[31mbar",  # ANSI escape
        "foo\x7fbar",  # DEL
        "foo\x85bar",  # C1 NEL (treated as a line break by log shippers)
        "foo\u2028bar",  # unicode line separator
        "foo\u2029bar",  # unicode paragraph separator
    ],
)
def test_validate_search_query_rejects_control_characters(dirty):
    with pytest.raises(InvalidQuerySyntaxError, match="control characters"):
        validate_search_query(dirty)


def test_validate_search_query_accepts_plain_text():
    validate_search_query("hello world")
    validate_search_query("from:alice has:image")
    # Filter-only queries are still valid when the caller says filters carry
    # the request; that path must not start rejecting ordinary needles.
    validate_search_query("", has_filters=True)


def test_control_char_rejected_even_with_filters_flag():
    # The empty-query opt-out must not become a control-char bypass.
    with pytest.raises(InvalidQuerySyntaxError, match="control characters"):
        validate_search_query("hello\x00", has_filters=True)


def test_overflowing_relative_date_is_clamped_not_crashed():
    parser = QueryParser()
    # 20 digits of days: int() succeeds, timedelta() used to OverflowError.
    parsed = parser.parse("after:99999999999999999999d")

    date_filters = [f for f in parsed.filters if f.filter_type == FilterType.AFTER_DATE]
    assert len(date_filters) == 1
    # The value is a real ISO date, not the raw overflowing digit string.
    assert len(date_filters[0].value) == len("YYYY-MM-DD")
    assert date_filters[0].value[4] == "-"
    assert date_filters[0].value[0].isdigit()  # digits throughout


@pytest.mark.parametrize("unit", ["d", "w", "m", "y"])
def test_every_relative_date_unit_clamps_the_span(unit):
    parser = QueryParser()
    parsed = parser.parse(f"before:99999999999999999999{unit}")

    date_filters = [f for f in parsed.filters if f.filter_type == FilterType.BEFORE_DATE]
    assert len(date_filters) == 1
    assert len(date_filters[0].value) == len("YYYY-MM-DD")


def test_normal_relative_dates_still_resolve():
    parser = QueryParser()
    parsed = parser.parse("after:7d before:2w")
    values = {f.filter_type: f.value for f in parsed.filters}
    assert FilterType.AFTER_DATE in values
    assert FilterType.BEFORE_DATE in values
    # A one-week span must be far smaller than the clamped century ceiling.
    assert values[FilterType.AFTER_DATE] > values[FilterType.BEFORE_DATE]


def test_clamp_constant_is_a_sane_century():
    assert MAX_RELATIVE_DATE_DAYS > 0
    assert MAX_RELATIVE_DATE_DAYS < 100_000  # stays well inside datetime's range
