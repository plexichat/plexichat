"""SECURITY: server-scoped user search must not leak a roster to non-members.

``/search/users?server_id=X`` resolves the server's member set as its
visibility scope. Before this fix the scope was applied only as a *filter on
hits*: any authenticated caller could name a server id and receive its member
list, even when the caller was not a member (and even for private servers).
The message-search sibling already gates server scoping with
``_can_access_server``; server-scoped user search had no equivalent check.

The gate must also run *before* the result-cache lookup so a denied request can
never read or prime a memo.

These tests use lightweight fakes (no full application bootstrap) and an
indexer that always returns the private member, so they are non-vacuous: a
build without the membership gate leaks that member and fails the tests.
"""

from __future__ import annotations

import pytest

from src.core.search.manager.user_search import UserSearchMixin
from src.core.search.models import UserSearchResult
from src.core.search.query import RankingEngine

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.search]

CALLER = 777  # authenticated user issuing the search
PRIVATE_MEMBER = 500  # member of server 42, unknown to the caller
SERVER_ID = 42


class _LeakingIndexer:
    """Reports the private member as a hit regardless of the caller."""

    def search_users(self, query, limit=25, offset=0):
        return [
            UserSearchResult(
                id=PRIVATE_MEMBER,
                user_id=PRIVATE_MEMBER,
                username="private-member",
            )
        ]

    def search_users_page(self, query, limit=25, cursor=None):
        return self.search_users(query, limit=limit), None


class _NoRowsDB:
    """No supplemental directory matches, so hits come only from the indexer."""

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        return []


class _ScopeHost(UserSearchMixin):
    def __init__(self, server_members):
        self._config = {"result_limit": 100}
        self._indexer = _LeakingIndexer()
        self._db = _NoRowsDB()
        self._ranking_engine = RankingEngine()
        self._server_members = set(server_members)
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _get_server_member_ids(self, server_id):
        return set(self._server_members)

    def _enrich_user_results(self, results, user_id):
        return results


def test_scoped_user_search_denies_caller_outside_server():
    """A non-member must not enumerate the server's members (offset path)."""
    host = _ScopeHost(server_members={PRIVATE_MEMBER})  # CALLER not included

    results = host.search_users(
        user_id=CALLER, query="private", server_id=SERVER_ID, limit=10
    )

    assert results == [], "server scope leaked a roster to a non-member"


def test_scoped_user_search_page_denies_caller_outside_server():
    """Same gate on the paged path used by /search/users."""
    host = _ScopeHost(server_members={PRIVATE_MEMBER})

    page = host.search_users_page(
        user_id=CALLER, query="private", server_id=SERVER_ID, limit=10
    )

    assert page.results == [], "paged server scope leaked a roster to a non-member"


def test_scoped_user_search_allows_a_real_member():
    """The gate must not disable scoped search for legitimate members."""
    host = _ScopeHost(server_members={PRIVATE_MEMBER, CALLER})

    results = host.search_users(
        user_id=CALLER, query="private", server_id=SERVER_ID, limit=10
    )

    assert any(r.user_id == PRIVATE_MEMBER for r in results)


def test_denied_request_does_not_prime_cache_for_later_member_access():
    """A denied search must not poison the memo for a later valid scope."""
    host = _ScopeHost(server_members={PRIVATE_MEMBER})
    host.search_users_page(
        user_id=CALLER, query="private", server_id=SERVER_ID, limit=10
    )

    # The caller joins; the next search must succeed from authoritative data.
    host._server_members = {PRIVATE_MEMBER, CALLER}
    page = host.search_users_page(
        user_id=CALLER, query="private", server_id=SERVER_ID, limit=10
    )

    assert any(r.user_id == PRIVATE_MEMBER for r in page.results)
