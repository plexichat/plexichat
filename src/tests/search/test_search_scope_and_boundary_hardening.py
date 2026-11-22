"""Regressions for two residual search weaknesses.

1. A ``server_id``-scoped user search applied the membership filter only to
   indexer hits. The supplemental directory fallback then re-added users from
   the global ``auth_users`` table AFTER that filter, so a caller could escape
   the server scope and receive users who are not members of it.

2. The manager/HTTP layer rejects control characters, but indexers are directly
   callable (rebuilds, tests, internal callers). Only the SQLite FTS5 indexer
   enforced this at its own boundary; the Postgres, Elasticsearch and
   Meilisearch backends trusted the caller. A NUL in a bound Postgres string
   raises a driver error that 500s the search. Every backend now fails closed
   through the shared ``bounded_query`` helper.
"""

from __future__ import annotations

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.search.indexer.base import bounded_query
from src.core.search.indexer.elasticsearch import ElasticsearchIndexer
from src.core.search.indexer.meilisearch import MeilisearchIndexer
from src.core.search.indexer.postgres import PostgresIndexer
from src.core.search.manager.user_search import UserSearchMixin
from src.core.search.models import UserSearchResult
from src.core.search.query.ranking import RankingEngine

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.search]


# --- 1. server scope cannot be widened by the directory fallback -------------


class _ScopedIndexer:
    """Indexer that reports an out-of-scope user as an FTS match."""

    def __init__(self, outsider_id: int):
        self.outsider_id = outsider_id

    def search_users(self, query, limit=25, offset=0):
        return [
            UserSearchResult(
                id=self.outsider_id, user_id=self.outsider_id, username="outsider"
            )
        ]

    def search_users_page(self, query, limit=25, cursor=None):
        return self.search_users(query, limit=limit), None


class _DirectoryDB:
    """DB double whose auth_users lookup returns the out-of-scope user."""

    def __init__(self, outsider_id: int):
        self.outsider_id = outsider_id
        self.calls = []

    def fetch_one(self, sql, params=None):
        self.calls.append((sql, params))
        return None

    def fetch_all(self, sql, params=None):
        self.calls.append((sql, params))
        if "auth_users" in sql:
            return [{"id": self.outsider_id, "username": "outsider"}]
        return []


class _ScopedUserSearch(UserSearchMixin):
    """Minimal mixin host with the collaborators stubbed out."""

    def __init__(self, outsider_id: int, server_members):
        self._config = {"result_limit": 100}
        self._indexer = _ScopedIndexer(outsider_id)
        self._db = _DirectoryDB(outsider_id)
        self._server_members = server_members
        self._ranking_engine = RankingEngine()

    def _check_rate_limit(self, user_id):
        pass

    def _get_server_member_ids(self, server_id):
        return self._server_members

    def _enrich_user_results(self, results, user_id):
        return results


OUTSIDER_ID = 9001
SERVER_MEMBERS = {100, 200}  # owner + a real member; OUTSIDER_ID is absent


def test_server_scoped_user_search_excludes_non_member_from_fallback():
    mgr = _ScopedUserSearch(OUTSIDER_ID, SERVER_MEMBERS)

    results = mgr.search_users(user_id=100, query="outsider", server_id=42, limit=10)

    assert all(r.user_id != OUTSIDER_ID for r in results), (
        "server-scoped search leaked a user who is not a member of the server"
    )


def test_unscoped_user_search_still_returns_directory_match():
    # The fix must not disable the fallback for genuinely global searches.
    mgr = _ScopedUserSearch(OUTSIDER_ID, SERVER_MEMBERS)

    results = mgr.search_users(user_id=100, query="outsider", limit=10)

    assert any(r.user_id == OUTSIDER_ID for r in results)


def test_server_scoped_user_search_page_excludes_non_member_from_fallback():
    mgr = _ScopedUserSearch(OUTSIDER_ID, SERVER_MEMBERS)

    page = mgr.search_users_page(user_id=100, query="outsider", server_id=42, limit=10)

    assert all(r.user_id != OUTSIDER_ID for r in page.results)


# --- 2. every indexer rejects control chars at its own boundary -------------


class _RecordingClient:
    def __init__(self):
        self.requests = []

    def request(self, method, path, body=None):
        self.requests.append((method, path, body))
        return {}


class _RecordingDB:
    type = "postgres"

    def __init__(self):
        self.selects = []

    def execute(self, sql, params=None):
        return None

    def fetch_one(self, sql, params=None):
        return {"1": 1}

    def fetch_all(self, sql, params=None):
        self.selects.append((sql, params))
        return []


DIRTY = ["he\x00llo", "a\x1fb", "x\x7f", "line\u2028sep"]
SEARCH_METHODS = ["search_messages", "search_users", "search_servers"]


@pytest.mark.parametrize("needle", DIRTY)
def test_bounded_query_rejects_control_chars(needle):
    with pytest.raises(HardeningViolation):
        bounded_query(needle)


@pytest.mark.parametrize("needle", DIRTY)
@pytest.mark.parametrize("method", SEARCH_METHODS)
def test_elasticsearch_rejects_control_chars(needle, method):
    client = _RecordingClient()
    indexer = ElasticsearchIndexer(http_client=client)
    indexer._initialized = True

    with pytest.raises(HardeningViolation):
        getattr(indexer, method)(needle)
    assert client.requests == [], "no request may reach the backend"


@pytest.mark.parametrize("needle", DIRTY)
@pytest.mark.parametrize("method", SEARCH_METHODS)
def test_meilisearch_rejects_control_chars(needle, method):
    client = _RecordingClient()
    indexer = MeilisearchIndexer(http_client=client)
    indexer._initialized = True

    with pytest.raises(HardeningViolation):
        getattr(indexer, method)(needle)
    assert client.requests == [], "no request may reach the backend"


@pytest.mark.parametrize("needle", DIRTY)
@pytest.mark.parametrize("method", SEARCH_METHODS)
def test_postgres_rejects_control_chars(needle, method):
    db = _RecordingDB()
    indexer = PostgresIndexer(db)
    indexer._initialized = True

    with pytest.raises(HardeningViolation):
        getattr(indexer, method)(needle)
    assert db.selects == [], "no SQL may reach the backend"
