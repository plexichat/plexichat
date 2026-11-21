"""PERF: server-scoped user search resolves the member scope once per roster.

BEFORE: ``AccessControlMixin._get_server_member_ids`` issued a fresh
``SELECT user_id FROM srv_members WHERE server_id = ?`` full projection on
every call. Server-scoped user search (including typeahead) calls it on EVERY
keystroke because the visibility scope is folded into the result-cache key,
which is intentionally resolved *before* the cache lookup. A burst over a
large server therefore replayed the whole roster read for every request.

AFTER: the identical, bounded, short-TTL, write-invalidated member-id cache
already used by gateway/presence/voice fan-out backs this lookup. The scope is
server-keyed (never keyed by the searching credential), so no user's
authorization can leak into another's search, and every membership write
clears it through the shared funnel.

These tests use lightweight fakes so they run as fast unit tests without the
full application bootstrap.
"""

import pytest

from src.core.search.manager.access import AccessControlMixin
from src.core.search.manager.user_search import UserSearchMixin
from src.core.search.models import UserSearchResult
from src.core.search.query import RankingEngine
from src.core.servers.manager.member_id_cache import (
    get_cached_member_user_ids,
    invalidate_member_id_cache,
)

pytestmark = [pytest.mark.unit, pytest.mark.search]

N = 25


@pytest.fixture(autouse=True)
def _clean_member_id_cache():
    """Isolate the process-local L0 so each test starts from a cold scope."""
    invalidate_member_id_cache()
    yield
    invalidate_member_id_cache()


class _CountingDB:
    """Counts only the srv_members scope projection; other reads return empty."""

    def __init__(self, members=None):
        self.members = list(members or ())
        self.member_queries = 0

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        if "srv_members" in sql and "server_id" in sql and "user_id" in sql:
            self.member_queries += 1
            # Emulate a large roster: the old code paid O(rows) per keystroke.
            return [{"user_id": uid} for uid in self.members]
        return []


class _ScopeHost(AccessControlMixin):
    def __init__(self, db):
        self._db = db


# --- measurable reduction ---------------------------------------------------


def test_member_scope_resolved_once_across_burst():
    db = _CountingDB(range(1, 5001))
    host = _ScopeHost(db)

    for _ in range(N):
        scope = host._get_server_member_ids(42)

    assert len(scope) == 5000
    # BEFORE: N. The whole 5k-row projection was re-read on every lookup.
    assert db.member_queries == 1, (
        f"{N} scope lookups issued {db.member_queries} roster reads"
    )


def test_member_scope_values_are_equivalent_to_authoritative_rows():
    db = _CountingDB([10, 20, 30])
    host = _ScopeHost(db)

    assert host._get_server_member_ids(42) == {10, 20, 30}
    assert get_cached_member_user_ids(42) == [10, 20, 30]


# --- authz coherence: invalidation and per-server isolation ------------------


def test_membership_change_is_reflected_after_invalidation():
    db = _CountingDB([10, 20])
    host = _ScopeHost(db)

    assert host._get_server_member_ids(42) == {10, 20}
    assert host._get_server_member_ids(42) == {10, 20}  # cached
    assert db.member_queries == 1

    # A kick/leave evicts the shared namespace; the very next scoped search
    # must see the fresh roster, not a removed member still counted as visible.
    db.members = [10]
    invalidate_member_id_cache()
    assert host._get_server_member_ids(42) == {10}
    assert db.member_queries == 2


def test_scope_cache_is_per_server_no_cross_server_leak():
    db = _CountingDB([10, 20])
    host = _ScopeHost(db)
    db.members = [10, 20]

    assert host._get_server_member_ids(42) == {10, 20}

    db.members = [99]
    assert host._get_server_member_ids(43) == {99}

    # Server 42 must still resolve to its own cached roster.
    assert host._get_server_member_ids(42) == {10, 20}
    assert db.member_queries == 2


# --- end-to-end through user search -----------------------------------------


class _FakeUserIndexer:
    def search_users(self, query, limit=25, offset=0):
        return [
            UserSearchResult(id=i, user_id=i, username=f"{query}-{i}")
            for i in range(1, max(limit, 1) + 1)
        ]

    def search_users_page(self, query, limit=25, cursor=None):
        return self.search_users(query, limit), None


class _SearchHost(UserSearchMixin, AccessControlMixin):
    def __init__(self, db):
        self._config = {"result_limit": 100}
        self._db = db
        self._indexer = _FakeUserIndexer()
        self._ranking_engine = RankingEngine()
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _enrich_user_results(self, results, user_id):
        for r in results:
            r.display_name = f"viewer-{user_id}"
        return results


def test_typeahead_burst_uses_one_roster_read_and_preserves_authz():
    db = _CountingDB(range(1, 2001))
    host = _SearchHost(db)

    # Repeated server-scoped searches (the autocomplete pattern) must not
    # re-read the roster on each call.
    for _ in range(N):
        results = host.search_users(user_id=1, query="nee", server_id=42, limit=10)

    assert db.member_queries == 1
    assert len(results) == 10
    assert all(r.display_name == "viewer-1" for r in results)

    # A different viewer under the same server must not reuse the other's
    # results, and still must not re-read the roster.
    other = host.search_users(user_id=2, query="nee", server_id=42, limit=10)
    assert all(r.display_name == "viewer-2" for r in other)
    assert db.member_queries == 1
