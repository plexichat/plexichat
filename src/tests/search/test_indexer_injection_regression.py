"""Injection regression matrix for the search indexers.

User query text, categories and filter values are data, never SQL or filter
syntax. These tests drive quotes, semicolons, comment markers and unicode
through every indexer and assert the payload stays a bound value / escaped
filter literal and never appears as raw syntax in the constructed statement.
"""

import uuid

import pytest

from src.core.search.indexer.meilisearch import MeilisearchIndexer
from src.core.search.indexer.postgres import PostgresIndexer, _escape_like
from src.core.search.indexer.sqlite_fts import SQLiteFTS5Indexer

pytestmark = [pytest.mark.unit, pytest.mark.security]

PAYLOADS = [
    "'",
    ";",
    "'; DROP TABLE search_messages; --",
    "' OR '1'='1",
    "/* comment */",
    "--",
    'a"; OR is_public = true OR "1"="1',
    "üsers",
    "users\u200b",
    "users\uff1b",
]


class _FakeDB:
    def __init__(self, websearch=True, dbtype="postgres"):
        self.websearch = websearch
        self.type = dbtype
        self.config = {"type": dbtype, "path": "/tmp/x.db"}
        self.selects = []

    def execute(self, query, params=None):
        return None

    def fetch_one(self, query, params=None):
        if "websearch_to_tsquery" in query and not self.websearch:
            raise RuntimeError("unsupported")
        return {"?": 1}

    def fetch_all(self, query, params=None):
        self.selects.append((query, params))
        return []


# --- PostgreSQL indexer: query text is always a bound parameter -------------


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize(
    "method", ["search_messages", "search_users", "search_servers"]
)
def test_postgres_search_binds_query(payload, method):
    db = _FakeDB()
    indexer = PostgresIndexer(db)
    indexer.initialize()

    # Use a unique marker so the assertion is unambiguous even for a payload
    # that is just a quote (the SQL template itself contains quotes).
    marker = "zz" + uuid.uuid4().hex[:8]
    sent = f"{payload} {marker}"
    getattr(indexer, method)(sent)

    sql, params = db.selects[-1]
    assert marker not in sql, "query must not be concatenated into SQL"
    assert params[0] == sent, "query must be the first bound parameter"
    assert sql.count("?") == len(params)


@pytest.mark.parametrize("payload", ["%", "_", "\\", "'; --", "ü"])
def test_postgres_like_pattern_still_escaped(payload):
    expected = payload.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    assert _escape_like(payload) == expected


# --- SQLite FTS indexer: MATCH expression is quoted and bound ---------------


@pytest.mark.parametrize("payload", PAYLOADS)
def test_sqlite_fts_query_is_quoted_terms(payload):
    indexer = SQLiteFTS5Indexer(_FakeDB(dbtype="sqlite"))
    fts = indexer._build_fts_query(payload)
    # All terms are wrapped in double quotes and the expression is a bound
    # MATCH parameter, so comment markers cannot open a clause. Quotes are
    # balanced (a quoted phrase may itself contain " OR ").
    assert "--" not in fts
    assert "/*" not in fts
    assert fts.count('"') % 2 == 0
    assert fts.startswith('"')


@pytest.mark.parametrize("payload", PAYLOADS)
def test_sqlite_fts_search_binds_match_expression(payload):
    db = _FakeDB(dbtype="sqlite")
    indexer = SQLiteFTS5Indexer(db)
    # Skip real FTS initialization; drive the query construction directly.
    indexer._initialized = True

    indexer.search_messages(payload)

    sql, params = db.selects[-1]
    assert "MATCH ?" in sql
    assert payload not in sql
    assert params[0] == indexer._build_fts_query(payload)


# --- Meilisearch: filter values escape quotes/backslashes -------------------


class _RecordingClient:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        return {}


@pytest.mark.parametrize("payload", PAYLOADS)
def test_meilisearch_filter_escapes_payload(payload):
    client = _RecordingClient()
    indexer = MeilisearchIndexer(http_client=client)
    indexer._initialized = True

    indexer.search_servers("q", category=payload)

    method, path, body = client.requests[-1]
    assert path.endswith("/search")
    filt = body["filter"]
    escaped = str(payload).replace("\\", "\\\\").replace('"', '\\"')
    assert f'category = "{escaped}"' in filt
