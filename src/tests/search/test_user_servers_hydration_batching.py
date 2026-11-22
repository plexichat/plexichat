"""Regression: per-hit server hydration must be batched, scope preserved.

``_get_user_servers_map`` previously issued one cache GET per hydrated
search hit and built a single unbounded ``WHERE user_id IN (...)`` bind list.
It now uses one bulk cache read (MGET) plus chunked SQL queries. These tests
pin the round-trip reduction, the bind cap, and the exact per-user scope
contract (no cross-user leakage, fail-closed on malformed cache data).
"""

from unittest.mock import patch

import pytest

from src.core.search.manager.access import AccessControlMixin

pytestmark = [pytest.mark.unit, pytest.mark.search]


class _MemberDB:
    """Fake DB that answers chunked IN (...) member lookups."""

    def __init__(self, members):
        self.members = members  # {uid: [server ids]}
        self.queries = []

    def fetch_all(self, sql, params=None):
        self.queries.append((sql, tuple(params or ())))
        rows = []
        for uid in params or ():
            for sid in self.members.get(uid, ()):
                rows.append({"user_id": uid, "server_id": sid})
        return rows


class _Manager(AccessControlMixin):
    """Only the mixin under test; no app bootstrap required."""

    def __init__(self, db):
        self._db = db


def _manager(members):
    mgr = _Manager(_MemberDB(members))
    return mgr, mgr._db


# --- SQL query-count / bind bound ------------------------------------------


def test_server_hydration_query_count_is_bounded_by_page_not_hits():
    members = {uid: [10, 20] for uid in range(1, 601)}
    for n, expected_queries in ((1, 1), (10, 1), (25, 1), (100, 1), (600, 2)):
        mgr, db = _manager(members)
        with patch("src.core.database.valkey_available", return_value=False):
            result = mgr._get_user_servers_map(list(range(1, n + 1)))
        # 500-bind chunk cap: N users cost ceil(N/500) SQL queries, not N.
        assert len(db.queries) == expected_queries, (n, len(db.queries))
        assert all("IN (" in sql for sql, _ in db.queries)
        assert len(result) == n


def test_server_hydration_scope_preserved_across_batches():
    """Exact server sets survive chunking; empty/no-hit users stay present."""
    members = {1: {10, 20}, 2: {30}, 501: {999}}
    mgr, db = _manager({uid: list(sids) for uid, sids in members.items()})
    requested = list(range(1, 502)) + [777]  # 502 ids -> two 500-bind chunks
    with patch("src.core.database.valkey_available", return_value=False):
        result = mgr._get_user_servers_map(requested)

    assert len(db.queries) == 2
    assert all(len(params) <= 500 for _, params in db.queries)
    assert set(result) == set(requested)
    assert result[1] == {10, 20}
    assert result[2] == {30}
    assert result[501] == {999}
    assert result[777] == set()  # no memberships -> present as empty set


# --- Cache round-trip reduction / isolation --------------------------------


def test_server_hydration_single_mget_replaces_n_gets():
    """Warm cache: one bulk read, no per-user GET round-trips, no SQL."""
    cache = {f"user:servers:{uid}": [10 + uid] for uid in (1, 2, 3)}
    mgr, db = _manager({})
    with (
        patch("src.core.database.valkey_available", return_value=True),
        patch(
            "src.core.database.cache_get_many",
            side_effect=lambda keys: {k: cache[k] for k in keys if k in cache},
        ) as mget,
        patch("src.core.database.cache_get") as single_get,
        patch("src.core.database.cache_set"),
    ):
        result = mgr._get_user_servers_map([1, 2, 3])

    mget.assert_called_once()
    assert list(mget.call_args.args[0]) == [
        "user:servers:1",
        "user:servers:2",
        "user:servers:3",
    ]
    single_get.assert_not_called()
    assert db.queries == []
    assert result == {1: {11}, 2: {12}, 3: {13}}


def test_server_hydration_cold_and_warm_paths_agree_and_do_not_leak():
    """A cached value for one user never grants servers to another user."""
    members = {1: [10, 20], 2: [30], 3: []}
    cache = {
        # Only user 1 is warm; 2 and 3 must be resolved from SQL.
        "user:servers:1": [10, 20],
    }
    cold_mgr, _ = _manager(members)
    with patch("src.core.database.valkey_available", return_value=False):
        cold = cold_mgr._get_user_servers_map([1, 2, 3])

    warm_mgr, warm_db = _manager(members)
    with (
        patch("src.core.database.valkey_available", return_value=True),
        patch(
            "src.core.database.cache_get_many",
            side_effect=lambda keys: {k: cache[k] for k in keys if k in cache},
        ),
        patch("src.core.database.cache_set"),
    ):
        warm = warm_mgr._get_user_servers_map([1, 2, 3])

    assert warm == cold == {1: {10, 20}, 2: {30}, 3: set()}
    # Only the cache misses hit SQL, and only for the missing users.
    assert len(warm_db.queries) == 1
    assert sorted(warm_db.queries[0][1]) == [2, 3]


def test_server_hydration_malformed_cache_value_fails_closed():
    """A corrupt cached value must not become a broader server grant."""
    members = {1: [10], 2: [20]}
    cache = {"user:servers:1": "not-a-list"}
    mgr, db = _manager(members)
    with (
        patch("src.core.database.valkey_available", return_value=True),
        patch(
            "src.core.database.cache_get_many",
            side_effect=lambda keys: {k: cache[k] for k in keys if k in cache},
        ),
        patch("src.core.database.cache_set"),
    ):
        result = mgr._get_user_servers_map([1, 2])

    assert result == {1: {10}, 2: {20}}
    assert len(db.queries) == 1
    assert sorted(db.queries[0][1]) == [1, 2]
