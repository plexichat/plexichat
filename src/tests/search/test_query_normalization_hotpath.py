"""Regression guards for query-normalization work in the search hot path.

Two inefficiencies are pinned here:

1. ``QueryParser.parse`` used to scan each regex twice (``finditer`` to collect
   phrases/filters, then ``sub`` to strip them). A single ``sub`` with a
   collector callback now does both; these tests pin both result parity and
   the one-pass invariant.
2. The ``parse_query`` convenience helper used to build a fresh ``QueryParser``
   per call, re-deriving the filter pattern from ``FILTER_PATTERNS``. The
   normalization regexes are now precompiled once at module scope and the
   helper reuses one shared, stateless parser.

Lightweight call counters keep these as fast unit tests.
"""

import pytest

import src.core.search.query.parser as parser_module
from src.core.search.query.parser import QueryParser, parse_query
from src.core.search.models import FilterType

pytestmark = [pytest.mark.unit, pytest.mark.search]


# Exercises every normalization branch (phrases, filters, negation, terms).
_QUERIES = [
    "hello world",
    "from:alice in:general has:image pinned:true",
    'from:alice -has:link "exact phrase" hello after:2024-01-15',
    '"quoted one" "quoted two" tail',
    "",
    "   ",
    "before:2024-01-01 after:2024-12-31 mentions:bob",
    'in:#channel "multi word phrase" -from:mallory rest',
]


def test_parse_query_matches_fresh_parser():
    """The shared parser must produce exactly what a fresh parser produces."""
    for query in _QUERIES:
        assert parse_query(query) == QueryParser().parse(query)


def test_single_pass_normalization_result_parity():
    """Fixed-date query: collected filters/phrases/terms are unchanged."""
    parsed = parse_query(
        'from:alice -has:link "exact phrase" hello after:2024-01-15 pinned:true'
    )
    assert parsed.search_terms == ["hello"]
    assert parsed.exact_phrases == ["exact phrase"]
    assert [(f.filter_type, f.value, f.negated) for f in parsed.filters] == [
        (FilterType.FROM_USER, "alice", False),
        (FilterType.HAS_ATTACHMENT, "link", True),
        (FilterType.AFTER_DATE, "2024-01-15", False),
        (FilterType.PINNED, "true", False),
    ]


def test_each_regex_is_applied_once_per_parse():
    """No redundant finditer pass: one sub per pattern, zero finditer calls."""

    class _CountingPattern:
        def __init__(self, real):
            self._real = real
            self.sub_calls = 0
            self.finditer_calls = 0

        def sub(self, *args, **kwargs):
            self.sub_calls += 1
            return self._real.sub(*args, **kwargs)

        def finditer(self, *args, **kwargs):
            self.finditer_calls += 1
            return self._real.finditer(*args, **kwargs)

    parser = QueryParser()
    phrase = _CountingPattern(parser_module._PHRASE_REGEX)
    filt = _CountingPattern(parser_module._FILTER_REGEX)
    parser._phrase_regex = phrase
    parser._filter_regex = filt

    parsed = parser.parse('from:alice "exact phrase" hello')
    assert phrase.sub_calls == 1 and phrase.finditer_calls == 0
    assert filt.sub_calls == 1 and filt.finditer_calls == 0
    assert parsed.search_terms == ["hello"]
    assert parsed.exact_phrases == ["exact phrase"]
    assert len(parsed.filters) == 1


def test_regexes_compiled_once_and_shared_by_all_parsers():
    """Constructing parsers must not rebuild the derived filter pattern."""
    parsers = [QueryParser() for _ in range(20)]
    assert all(p._filter_regex is parser_module._FILTER_REGEX for p in parsers)
    assert all(p._phrase_regex is parser_module._PHRASE_REGEX for p in parsers)


def test_parse_query_uses_the_shared_default_parser():
    """parse_query must not construct a parser on every call."""
    original = parser_module.QueryParser
    constructed = []

    class _CountingParser(original):
        def __init__(self):
            constructed.append(1)
            super().__init__()

    parser_module.QueryParser = _CountingParser
    try:
        for _ in range(10):
            assert parse_query("hello from:alice").search_terms == ["hello"]
        # The module-level default parser was built at import time, so patching
        # the constructor afterwards cannot add work to parse_query calls.
        assert constructed == []
    finally:
        parser_module.QueryParser = original
