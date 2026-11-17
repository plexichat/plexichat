"""Regression: core search entry points bound their own page parameters.

The HTTP routes clamp these values, but the manager/discovery layer is also
called directly. SQLite reads a negative LIMIT as *unlimited*, so the bound
must be enforced at the core boundary as well as at the route boundary.
"""

import pytest

from src.core.search.discovery.manager import DiscoveryManager
from src.core.search.manager.query_utils import QueryUtilsMixin

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _Parser:
    def get_filter_suggestions(self, partial):
        return []


class _DB:
    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return []


class _Suggestions(QueryUtilsMixin):
    def __init__(self, db):
        self._db = db
        self._query_parser = _Parser()


def test_suggestions_negative_limit_is_an_empty_page():
    db = _DB()
    result = _Suggestions(db).get_search_suggestions(1, "hello", limit=-1)
    assert result == []
    assert db.calls == [], "no SQL must run for a non-positive limit"


def test_suggestions_huge_limit_is_clamped():
    db = _DB()
    _Suggestions(db).get_search_suggestions(1, "hello", limit=10**9)
    sql, params = db.calls[-1]
    assert "LIMIT ?" in sql
    assert params[-1] == 100, "unbounded LIMIT would dump all search history"


def test_suggestions_needle_length_is_bounded():
    db = _DB()
    _Suggestions(db).get_search_suggestions(1, "a" * 10000, limit=10)
    sql, params = db.calls[-1]
    prefix = params[1]
    assert prefix.endswith("%")
    assert len(prefix) <= 129, "LIKE needle must be bounded at the core boundary"


def test_suggestions_still_escape_like_wildcards():
    db = _DB()
    _Suggestions(db).get_search_suggestions(1, "%\\_", limit=10)
    sql, params = db.calls[-1]
    assert "ESCAPE '\\'" in sql
    assert params[1] == "\\%\\\\\\_%"


class _Discovery:
    """Bare DiscoveryManager: only list_public_servers is under test."""

    def __new__(cls, db):
        obj = object.__new__(DiscoveryManager)
        obj._db = db
        obj._config = {}
        return obj


def test_discovery_negative_limit_is_not_unlimited():
    db = _DB()
    _Discovery(db).list_public_servers(limit=-1)
    sql, params = db.calls[-1]
    assert "LIMIT ? OFFSET ?" in sql
    assert params[-2:] == (0, 0)


def test_discovery_huge_limit_is_clamped():
    db = _DB()
    _Discovery(db).list_public_servers(limit=10**9, offset=10**9)
    sql, params = db.calls[-1]
    assert params[-2:] == (1000, 1_000_000)
