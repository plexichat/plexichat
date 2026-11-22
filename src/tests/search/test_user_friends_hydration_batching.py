"""Regression: per-hit friend hydration must be batched, scope preserved.

``_get_user_friends_map`` previously issued one ``SELECT`` per hydrated
search hit (one per result user), so user search cost scaled linearly with
the page size. It now uses a single bulk cache read (MGET) plus chunked
``WHERE user_id IN (...)`` queries. These tests pin both the query-count
reduction and the exact friend/scope contract end-to-end.
"""

from unittest.mock import patch

import pytest

from src.core.search.manager.access import AccessControlMixin
from src.core.search.manager.enrichment import EnrichmentMixin
from src.core.search.models import UserSearchResult

pytestmark = [pytest.mark.unit, pytest.mark.search]


class _FriendDB:
    """Fake DB that answers both per-id and IN (...) friend lookups."""

    def __init__(self, friends):
        self.friends = friends  # {uid: [friend ids]}
        self.queries = []

    def fetch_all(self, sql, params=None):
        self.queries.append((sql, tuple(params or ())))
        rows = []
        for uid in params or ():
            for fid in self.friends.get(uid, ()):
                rows.append({"user_id": uid, "friend_id": fid})
        return rows


class _HydrationManager(EnrichmentMixin, AccessControlMixin):
    """Only the mixins under test; no app bootstrap required."""

    def __init__(self, db):
        self._db = db

    # EnrichmentMixin._enrich_user_results also needs these; keep them local
    # and deterministic so the test only measures friend-hydration work.
    def _get_user_server_ids(self, user_id):
        return {10, 20}

    def _get_user_friend_ids(self, user_id):
        return set(self._db.friends.get(user_id, ()))

    def _get_user_servers_map(self, user_ids):
        return {uid: {10} for uid in user_ids}


def _manager(friends):
    mgr = _HydrationManager(_FriendDB(friends))
    return mgr, mgr._db


# --- query-count reduction -------------------------------------------------


def test_friend_hydration_query_count_is_bounded_by_page_not_hits():
    friends = {uid: [uid + 1000, uid + 2000] for uid in range(1, 601)}

    for n, expected_queries in ((1, 1), (10, 1), (25, 1), (100, 1), (600, 2)):
        mgr, db = _manager(friends)
        with patch("src.core.database.valkey_available", return_value=False):
            result = mgr._get_user_friends_map(list(range(1, n + 1)))
        # 500-bind chunk cap: N users cost ceil(N/500) SQL queries, not N.
        assert len(db.queries) == expected_queries, (n, len(db.queries))
        assert all("IN (" in sql for sql, _ in db.queries)
        assert len(result) == n


def test_friend_hydration_scope_preserved_across_batches():
    """Exact friend sets survive chunking; empty/no-hit users stay present."""
    friends = {
        1: {2, 3},
        2: {1},
        501: {999},  # lands in the second bind chunk -> must not be dropped
    }
    mgr, db = _manager({uid: list(fids) for uid, fids in friends.items()})
    requested = list(range(1, 502)) + [777]  # 502 ids -> two 500-bind chunks
    with patch("src.core.database.valkey_available", return_value=False):
        result = mgr._get_user_friends_map(requested)

    assert len(db.queries) == 2
    assert all(len(params) <= 500 for _, params in db.queries)
    # Every requested id is present, with its exact friend set (or empty).
    assert set(result) == set(requested)
    assert result[1] == {2, 3}
    assert result[2] == {1}
    assert result[3] == set()
    assert result[501] == {999}
    assert result[777] == set()  # no friends -> present as empty set


def test_friend_hydration_cold_and_warm_paths_agree():
    """Bulk cache read returns exactly the same scope as the cold SQL path."""
    friends = {1: [2, 3], 2: [1], 3: []}
    cache = {
        # The authoritative relationship cache key (shared with
        # get_friend_ids so every friend mutation invalidates it).
        "user_friends:int:1": [2, 3],
        "user_friends:int:2": [1],
        # user 3 deliberately absent -> must be resolved via SQL
    }

    cold_mgr, _ = _manager(friends)
    with patch("src.core.database.valkey_available", return_value=False):
        cold = cold_mgr._get_user_friends_map([1, 2, 3])

    warm_mgr, warm_db = _manager(friends)
    with (
        patch("src.core.database.valkey_available", return_value=True),
        patch(
            "src.core.database.cache_get_many",
            side_effect=lambda keys: {k: cache[k] for k in keys if k in cache},
        ),
        patch("src.core.database.cache_set"),
    ):
        warm = warm_mgr._get_user_friends_map([1, 2, 3])

    assert warm == cold == {1: {2, 3}, 2: {1}, 3: set()}
    # Only the cache miss hits SQL, and it does so with a single IN query.
    assert len(warm_db.queries) == 1
    assert tuple(warm_db.queries[0][1]) == (3,)


def test_enrichment_mutual_friend_counts_unchanged():
    """End-to-end: hydrated mutual_friend counts match the raw graph."""
    # Searcher 100 follows {1, 2, 3}; mutual counts are intersections of
    # each hit's friend set with that set (friend links are directed).
    friends = {
        100: [1, 2, 3],
        1: [2, 3],  # 2 mutual (2, 3)
        2: [3],  # 1 mutual (3)
        3: [999],  # 0 mutual
    }
    mgr, db = _manager(friends)
    hits = [UserSearchResult(id=i, user_id=i, username=f"u{i}") for i in (1, 2, 3)]

    with patch("src.core.database.valkey_available", return_value=False):
        enriched = mgr._enrich_user_results(hits, 100)

    # One batched query for the three hit users (not three per-hit queries).
    assert len(db.queries) == 1
    assert [r.mutual_friends for r in enriched] == [2, 1, 0]
