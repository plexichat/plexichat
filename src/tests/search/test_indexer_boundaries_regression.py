"""Regression tests: search indexer query/result boundaries.

The manager clamps query length and result windows at its entry points, but
indexers are directly callable (rebuilds, tests, internal callers). SQLite FTS5
has enforced its own bounds for a while; the Elasticsearch, Meilisearch and
Postgres backends did not and forwarded unbounded query strings and
limit/offset values straight to the engine. A multi-KB needle burns CPU in
backend query planning and a huge (or negative) LIMIT turns one request into a
full-index read. Every backend now clamps at the indexer boundary via the
shared helpers in ``indexer.base``.
"""

import pytest

from src.core.search.indexer.base import (
    MAX_BACKEND_QUERY_CHARS,
    MAX_BACKEND_RESULTS,
    MAX_BACKEND_OFFSET,
    bounded_page,
    bounded_query,
)
from src.core.security.hardening import HardeningViolation
from src.core.search.indexer.elasticsearch import ElasticsearchIndexer
from src.core.search.indexer.meilisearch import MeilisearchIndexer
from src.core.search.indexer.postgres import PostgresIndexer

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _RecordingClient:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        return {}


class _FakeDB:
    def __init__(self):
        self.selects = []

    def execute(self, query, params=None):
        return None

    def fetch_one(self, query, params=None):
        return {"1": 1}  # websearch_to_tsquery is supported

    def fetch_all(self, query, params=None):
        self.selects.append((query, params))
        return []


# --- shared helpers ---------------------------------------------------------


def test_bounded_query_truncates_and_stringifies():
    assert bounded_query("a" * 1000) == "a" * MAX_BACKEND_QUERY_CHARS
    assert bounded_query(None) == ""
    assert bounded_query(12345) == "12345"
    assert bounded_query(" normal ") == " normal "


def test_bounded_page_clamps_huge_and_negative_values():
    assert bounded_page(10**9, 10**9) == (MAX_BACKEND_RESULTS, MAX_BACKEND_OFFSET)
    assert bounded_page(-5, -5) == (1, 0)
    assert bounded_page(25, 0) == (25, 0)


def test_bounded_page_rejects_non_finite_and_bad_types():
    with pytest.raises(HardeningViolation):
        bounded_page(float("inf"), 0)
    with pytest.raises(HardeningViolation):
        bounded_page(10, float("nan"))
    assert bounded_page("not-an-int", None) == (25, 0)


# --- Elasticsearch ----------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["search_messages", "search_users", "search_servers"]
)
def test_elasticsearch_bounds_query_and_page(method):
    client = _RecordingClient()
    indexer = ElasticsearchIndexer(http_client=client)

    getattr(indexer, method)("q" * 500, limit=10**9, offset=10**9)

    _method, _path, body = client.requests[-1]
    assert body["size"] == MAX_BACKEND_RESULTS
    assert body["from"] == MAX_BACKEND_OFFSET

    expected = "q" * MAX_BACKEND_QUERY_CHARS
    if method == "search_users":
        assert body["query"]["multi_match"]["query"] == expected
    else:
        clause = body["query"]["bool"]["must"][0]
        if method == "search_messages":
            assert clause["match"]["content"] == expected
        else:
            assert clause["multi_match"]["query"] == expected


# --- Meilisearch ------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["search_messages", "search_users", "search_servers"]
)
def test_meilisearch_bounds_query_and_page(method):
    client = _RecordingClient()
    indexer = MeilisearchIndexer(http_client=client)

    getattr(indexer, method)("q" * 500, limit=10**9, offset=10**9)

    _method, _path, body = client.requests[-1]
    assert body["limit"] == MAX_BACKEND_RESULTS
    assert body["offset"] == MAX_BACKEND_OFFSET
    assert body["q"] == "q" * MAX_BACKEND_QUERY_CHARS


# --- Postgres ---------------------------------------------------------------


@pytest.mark.parametrize(
    "method", ["search_messages", "search_users", "search_servers"]
)
def test_postgres_bounds_query_and_page(method):
    db = _FakeDB()
    indexer = PostgresIndexer(db)
    indexer._initialized = True

    getattr(indexer, method)("q" * 500, limit=10**9, offset=10**9)

    _sql, params = db.selects[-1]
    assert params[0] == "q" * MAX_BACKEND_QUERY_CHARS
    assert params[-2] == MAX_BACKEND_RESULTS
    assert params[-1] == MAX_BACKEND_OFFSET
