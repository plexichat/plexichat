"""PERF: user search memoizes its final result like message/server search.

BEFORE: every identical ``search_users`` / ``search_users_page`` call replayed
the backend FTS lookup, the supplemental LIKE scan, the per-user mutual
server/friend hydration and ranking. N identical keystrokes/requests meant N
backend queries and N hydrations.

AFTER: the final page is memoized for the same short TTL as the other two
search paths under a key carrying the subject user id AND (for server-scoped
searches) the resolved member set, so no scope can reuse another's entry. The
``get_result_cache`` primitive is already bounded and deep-copies values in/out.

The tests use lightweight fakes (no full application bootstrap) so they run as
fast unit tests and count real calls through the mixin pipeline.
"""

import pytest

from src.core.search.manager.user_search import UserSearchMixin
from src.core.search.models import UserSearchResult, UserSearchResultPage
from src.core.search.query import RankingEngine

pytestmark = [pytest.mark.unit, pytest.mark.search]

N = 20


class _FakeUserIndexer:
    def __init__(self):
        self.calls = 0

    def search_users(self, query, limit=25, offset=0):
        self.calls += 1
        return [
            UserSearchResult(id=i, user_id=i, username=f"{query}-{i}")
            for i in range(1, max(limit, 1) + 1)
        ]

    def search_users_page(self, query, limit=25, cursor=None):
        self.calls += 1
        return [
            UserSearchResult(id=i, user_id=i, username=f"{query}-{i}")
            for i in range(1, max(limit, 1) + 1)
        ], None


class _NoRowsDB:
    """Directory fallback DB double: no supplemental matches."""

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        return []


class _UserHost(UserSearchMixin):
    def __init__(self, server_members=None):
        self._config = {"result_limit": 100}
        self._indexer = _FakeUserIndexer()
        self._ranking_engine = RankingEngine()
        self._db = _NoRowsDB()
        self._server_members = server_members
        self.enrich_calls = 0
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _get_server_member_ids(self, server_id):
        return set(self._server_members or ())

    def _enrich_user_results(self, results, user_id):
        # Stand-in for the real mutual-count hydration. Stamping the viewer
        # makes a cross-user cache leak visible in the returned results.
        self.enrich_calls += 1
        for r in results:
            r.display_name = f"viewer-{user_id}"
        return results


# --- measurable reduction (the query counter) --------------------------------


def test_identical_queries_collapse_backend_and_hydration_calls():
    # BEFORE: with the memo disabled (TTL=0), N identical searches replay the
    # whole pipeline N times.
    before_host = _UserHost()
    before_host._config["result_cache_ttl_seconds"] = 0
    for _ in range(N):
        before_host.search_users(user_id=1, query="needle", limit=10)
    before_backend = before_host._indexer.calls
    before_enrich = before_host.enrich_calls
    assert before_backend == N
    assert before_enrich == N

    # AFTER: the same N searches over the bounded short-TTL memo.
    host = _UserHost()
    for _ in range(N):
        results = host.search_users(user_id=1, query="needle", limit=10)
    assert before_backend > host._indexer.calls
    assert before_enrich > host.enrich_calls
    assert host._indexer.calls == 1, (
        f"{N} identical searches made {host._indexer.calls} backend calls"
    )
    assert host.enrich_calls == 1, "hydration replayed behind the memo"
    assert len(results) == 10


def test_page_queries_collapse_and_next_cursor_preserved():
    host = _UserHost()
    for _ in range(N):
        page = host.search_users_page(user_id=1, query="needle", limit=10)
    assert host._indexer.calls == 1
    assert host.enrich_calls == 1
    assert isinstance(page, UserSearchResultPage)
    assert page.next_cursor is None
    assert len(page.results) == 10


# --- authz: per-user keys and scope changes ----------------------------------


def test_same_query_different_users_do_not_share_results():
    host = _UserHost()
    first = host.search_users(user_id=1, query="needle", limit=10)
    second = host.search_users(user_id=2, query="needle", limit=10)
    assert first[0].display_name == "viewer-1"
    assert second[0].display_name == "viewer-2"
    assert host._indexer.calls == 2  # one per authz scope, never shared
    assert host.enrich_calls == 2


def test_server_membership_change_changes_scope_and_misses():
    # Caller 1 must be a member of the scope for a scoped search to be allowed.
    host = _UserHost(server_members={1, 10, 20})
    host.search_users(user_id=1, query="needle", server_id=42, limit=10)
    host.search_users(user_id=1, query="needle", server_id=42, limit=10)
    assert host._indexer.calls == 1

    # A permission change alters the resolved member set; the key includes it
    # so the stale entry can never be reused for the new scope.
    host._server_members = {1, 10, 20, 30}
    host.search_users(user_id=1, query="needle", server_id=42, limit=10)
    assert host._indexer.calls == 2


def test_server_page_scoped_search_is_also_per_user():
    # Both callers are members of the scope; per-user keys keep them separate.
    host = _UserHost(server_members={1, 2, 10, 20})
    host.search_users_page(user_id=1, query="needle", server_id=42, limit=10)
    host.search_users_page(user_id=2, query="needle", server_id=42, limit=10)
    assert host._indexer.calls == 2


# --- cache safety / hygiene --------------------------------------------------


def test_clear_cache_forces_recomputation():
    host = _UserHost()
    host.search_users(1, "needle", limit=10)
    host.search_users(1, "needle", limit=10)
    assert host._indexer.calls == 1

    host.clear_cache()
    host.search_users(1, "needle", limit=10)
    assert host._indexer.calls == 2


def test_cached_results_are_deep_copied():
    host = _UserHost()
    first = host.search_users(1, "needle", limit=10)
    first[0].display_name = "tampered"
    second = host.search_users(1, "needle", limit=10)
    assert second[0].display_name == "viewer-1"
