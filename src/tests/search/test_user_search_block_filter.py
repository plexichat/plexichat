"""SECURITY: user search must not surface two-way blocked users.

Every other cross-user read path in this codebase treats a two-way block
(the union of "users I blocked" and "users who blocked me") as a hard
visibility deny: profiles (``_is_blocked_between``), presence, DM send,
typing, voice and reactions all exclude a blocked counterparty.  User
search was the outlier: it returned blocked users from both the FTS index
and the supplemental directory fallback, so a blocked party could still be
found through ``/search/users`` (a cross-user privacy leak).

These tests are non-vacuous: the indexer always returns the blocked user,
so on an unfixed build the assertions below fail.  Run with::

    pytest src/tests/search/test_user_search_block_filter.py -q -n 0
"""

from __future__ import annotations

import pytest

from src.core.search.manager.user_search import UserSearchMixin
from src.core.search.models import UserSearchResult
from src.core.search.query import RankingEngine

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.search]

CALLER = 777
BLOCKED = 500  # either blocked by CALLER or blocking CALLER
ALLOWED = 501


class _LeakingIndexer:
    """Always reports both users, so block filtering is observable."""

    def __init__(self):
        self.calls = 0

    @staticmethod
    def _rows():
        return [
            UserSearchResult(id=BLOCKED, user_id=BLOCKED, username="blocked-user"),
            UserSearchResult(id=ALLOWED, user_id=ALLOWED, username="allowed-user"),
        ]

    def search_users(self, query, limit=25, offset=0):
        self.calls += 1
        return list(self._rows())

    def search_users_page(self, query, limit=25, cursor=None):
        self.calls += 1
        return list(self._rows()), None


class _BlockDB:
    """Projects the two-way UNION query from configured (blocker, blocked) rows."""

    def __init__(self, block_rows=None, *, raise_on_block=False):
        # Each entry is a (blocker_id, blocked_id) tuple, like rel_blocked.
        self.block_rows = list(block_rows or [])
        self.raise_on_block = raise_on_block

    def fetch_one(self, sql, params=None):
        return None

    def fetch_all(self, sql, params=None):
        if "rel_blocked" in sql:
            if self.raise_on_block:
                raise RuntimeError("relationships store unavailable")
            caller = params[0]
            projected = set()
            for blocker, blocked in self.block_rows:
                if blocker == caller:
                    projected.add(blocked)  # user the caller blocked
                if blocked == caller:
                    projected.add(blocker)  # user who blocked the caller
            return [{"user_id": uid} for uid in projected]
        # No supplemental directory matches.
        return []


class _BlockHost(UserSearchMixin):
    def __init__(self, block_rows=None, *, raise_on_block=False):
        self._config = {"result_limit": 100}
        self._indexer = _LeakingIndexer()
        self._db = _BlockDB(block_rows, raise_on_block=raise_on_block)
        self._ranking_engine = RankingEngine()
        self._search_rate_window_started_ms = {}
        self._search_rate_count = {}

    def _check_rate_limit(self, user_id):
        pass

    def _enrich_user_results(self, results, user_id):
        return results


def _row(blocker, blocked):
    return (blocker, blocked)


def test_user_who_blocked_caller_is_hidden_from_offset_search():
    """A user who blocked the caller must not be enumerable via search."""
    host = _BlockHost(block_rows=[_row(BLOCKED, CALLER)])

    results = host.search_users(CALLER, "user", limit=10)

    assert {r.user_id for r in results} == {ALLOWED}, (
        "search leaked a user who blocked the caller"
    )


def test_user_caller_blocked_is_hidden_from_page_search():
    """A user the caller blocked must not be enumerable via the paged path."""
    host = _BlockHost(block_rows=[_row(CALLER, BLOCKED)])

    page = host.search_users_page(CALLER, "user", limit=10)

    assert {r.user_id for r in page.results} == {ALLOWED}, (
        "paged search leaked a user the caller blocked"
    )


def test_block_is_applied_before_cache_and_recomputed_on_block_change():
    """A block added after a warm entry must not be served from that entry."""
    host = _BlockHost()
    first = host.search_users(CALLER, "user", limit=10)
    assert {r.user_id for r in first} == {BLOCKED, ALLOWED}  # non-vacuous warm state

    # The counterparty now blocks the caller; the very next search must hide it.
    host._db.block_rows = [_row(BLOCKED, CALLER)]
    second = host.search_users(CALLER, "user", limit=10)
    assert {r.user_id for r in second} == {ALLOWED}, (
        "stale cache entry resurrected a newly blocked user"
    )


def test_block_lookup_failure_fails_closed():
    """If the authoritative block set cannot be resolved, return nothing."""
    host = _BlockHost(raise_on_block=True)

    assert host.search_users(CALLER, "user", limit=10) == []
    page = host.search_users_page(CALLER, "user", limit=10)
    assert page.results == []
