"""Regressions for the batched friend and shared-server relationship lookups.

Audience hydration (presence, DM/gateway routing, notification batches) used to
resolve a relationship set once per user: an N+1 fan-out on cold caches and a
burst of per-key reads even when warm. The relationship manager now exposes
bounded bulk helpers whose results match the per-user lookups exactly.

These tests pin four properties:
* result parity with the per-user lookups, including fail-closed handling of
  unknown ids (empty set, never "everyone is a friend"/"shares a server");
* a query-count reduction from N per-id queries to one chunked query;
* hard bounds on IN-clause binds (<= 500 ids per query) so a large fan-out
  cannot build an unbounded statement;
* no cross-user leak: every result is keyed by the requested id, and stray
  rows for unrequested users are discarded rather than merged.
"""

from unittest.mock import patch
from uuid import uuid4

import pytest

from src.core.base import generate_id
from src.core.database.cache.base import mem_cache_clear_pattern


def _add_member(db, server_id, user_id):
    """Insert a membership directly, bypassing unrelated cache invalidation."""
    db.execute(
        "INSERT OR IGNORE INTO srv_members "
        "(id, server_id, user_id, joined_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (generate_id(), server_id, user_id, 0, 0),
    )


def _cold(db):
    """Start from cold caches so the query measurement cannot hide work."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


def _befriend(rel_manager, a, b):
    request = rel_manager.send_friend_request(a, b)
    rel_manager.accept_friend_request(b, request.id)


# --- friend sets -------------------------------------------------------------


@pytest.mark.relationships
def test_friend_ids_bulk_matches_per_user_and_cuts_queries(
    db, rel_manager, three_users
):
    u1, u2, u3 = three_users
    _befriend(rel_manager, u1.id, u2.id)
    _befriend(rel_manager, u3.id, u1.id)

    # Real users with real friend rows plus synthetic ids with none at all.
    ids = [u1.id, u2.id, u3.id] + [900000 + i for i in range(9)]

    _cold(db)
    per_user = {uid: set(rel_manager.get_friend_ids(uid)) for uid in ids}
    old_count = db.get_request_metrics()["query_count"]

    _cold(db)
    bulk = rel_manager.get_friend_ids_bulk(ids)
    new_count = db.get_request_metrics()["query_count"]

    assert {uid: set(values) for uid, values in bulk.items()} == per_user
    assert set(bulk) == set(ids)
    # Old path: one query per id. Batched path: one query for the whole batch.
    assert old_count >= len(ids)
    assert new_count == 1
    assert new_count < old_count


@pytest.mark.relationships
def test_friend_ids_bulk_empty_input_issues_no_queries(db, rel_manager):
    _cold(db)
    assert rel_manager.get_friend_ids_bulk([]) == {}
    assert db.get_request_metrics()["query_count"] == 0


@pytest.mark.relationships
def test_friend_ids_bulk_chunks_binds_at_500(db, rel_manager):
    ids = list(range(1, 1201))  # 1200 ids -> chunks of 500, 500, 200
    captured = []
    original_fetch_all = db.fetch_all

    def spy(query, params=None):
        captured.append((query, params))
        return original_fetch_all(query, params)

    _cold(db)
    with patch.object(db, "fetch_all", side_effect=spy):
        result = rel_manager.get_friend_ids_bulk(ids)

    assert set(result) == set(ids)
    assert len(captured) == 3, "1200 ids must resolve in exactly 3 chunks"
    chunk_sizes = []
    for query, params in captured:
        assert params is not None
        assert query.count("?") == len(params)
        assert query.count("?") <= 500  # hard IN-clause ceiling
        chunk_sizes.append(len(params))
    assert sorted(chunk_sizes) == [200, 500, 500]


@pytest.mark.relationships
def test_friend_ids_bulk_ignores_rows_for_unrequested_users(
    db, rel_manager, three_users
):
    """A stray row for another user must never merge into a requested set."""
    u1, u2, u3 = three_users
    _befriend(rel_manager, u1.id, u2.id)
    original_fetch_all = db.fetch_all

    def poisoned(query, params=None):
        rows = original_fetch_all(query, params)
        return list(rows) + [{"user_id": u3.id, "friend_id": u1.id}]

    _cold(db)
    with patch.object(db, "fetch_all", side_effect=poisoned):
        bulk = rel_manager.get_friend_ids_bulk([u1.id, u2.id])

    assert set(bulk) == {u1.id, u2.id}
    assert u3.id not in bulk
    assert set(bulk[u1.id]) == {u2.id}
    assert set(bulk[u2.id]) == {u1.id}
    assert u1.id not in bulk.get(u3.id, [])


@pytest.mark.relationships
def test_friend_ids_bulk_shares_invalidation_with_single_lookup(
    db, rel_manager, two_users
):
    """Bulk results must not outlive the cache invalidation of a mutation."""
    u1, u2 = two_users
    _befriend(rel_manager, u1.id, u2.id)
    _cold(db)

    assert u2.id in rel_manager.get_friend_ids_bulk([u1.id])[u1.id]

    # No manual cache clear here: remove_friend's own invalidation must drop
    # the shared "user_friends:int:<uid>" entry the bulk path warmed.
    assert rel_manager.remove_friend(u1.id, u2.id)
    assert rel_manager.get_friend_ids_bulk([u1.id])[u1.id] == []
    assert rel_manager.get_friend_ids(u1.id) == []


# --- shared servers ----------------------------------------------------------


@pytest.mark.relationships
def test_mutual_servers_bulk_matches_per_user_and_cuts_queries(
    db, rel_manager, server_manager, three_users
):
    u1, u2, u3 = three_users
    s1 = server_manager.create_server(u1.id, f"bulk-s1-{uuid4().hex[:8]}")
    _add_member(db, s1.id, u2.id)
    s2 = server_manager.create_server(u2.id, f"bulk-s2-{uuid4().hex[:8]}")
    _add_member(db, s2.id, u1.id)
    rel_manager._servers = server_manager

    targets = [u1.id, u2.id, u3.id, 920000]

    _cold(db)
    per_user = {t: set(rel_manager.get_mutual_servers(u1.id, t)) for t in targets}
    old_count = db.get_request_metrics()["query_count"]

    _cold(db)
    bulk = rel_manager.get_mutual_servers_bulk(u1.id, targets)
    new_count = db.get_request_metrics()["query_count"]

    assert {t: set(values) for t, values in bulk.items()} == per_user
    assert set(bulk) == set(targets)
    assert s1.id in bulk[u2.id]
    assert s2.id in bulk[u2.id]
    assert bulk[u3.id] == []
    assert bulk[920000] == []
    assert old_count >= len(targets)
    assert new_count == 1
    assert new_count < old_count


@pytest.mark.relationships
def test_mutual_servers_bulk_chunks_binds_at_500(
    db, rel_manager, server_manager, test_user
):
    rel_manager._servers = server_manager
    targets = list(range(1, 1201))  # viewer + 500/500/200 targets
    captured = []
    original_fetch_all = db.fetch_all

    def spy(query, params=None):
        captured.append((query, params))
        return original_fetch_all(query, params)

    _cold(db)
    with patch.object(db, "fetch_all", side_effect=spy):
        result = rel_manager.get_mutual_servers_bulk(test_user.id, targets)

    assert set(result) == set(targets)
    assert len(captured) == 3, "1200 targets must resolve in exactly 3 chunks"
    chunk_sizes = []
    for query, params in captured:
        assert params is not None
        assert query.count("?") == len(params)
        # One viewer bind up front, then at most 500 target ids in the IN.
        assert len(params) - 1 <= 500
        chunk_sizes.append(len(params) - 1)
    assert sorted(chunk_sizes) == [200, 500, 500]


@pytest.mark.relationships
def test_mutual_servers_bulk_ignores_rows_for_unrequested_users(
    db, rel_manager, server_manager, three_users
):
    u1, u2, u3 = three_users
    s1 = server_manager.create_server(u1.id, f"bulk-leak-{uuid4().hex[:8]}")
    _add_member(db, s1.id, u2.id)
    rel_manager._servers = server_manager
    original_fetch_all = db.fetch_all

    def poisoned(query, params=None):
        rows = original_fetch_all(query, params)
        return list(rows) + [{"target_id": u3.id, "server_id": s1.id}]

    _cold(db)
    with patch.object(db, "fetch_all", side_effect=poisoned):
        bulk = rel_manager.get_mutual_servers_bulk(u1.id, [u2.id])

    assert set(bulk) == {u2.id}
    assert u3.id not in bulk
    assert set(bulk[u2.id]) == {s1.id}


@pytest.mark.relationships
def test_mutual_servers_bulk_fails_closed_without_servers_module(
    db, rel_manager, three_users
):
    u1, u2, u3 = three_users
    rel_manager._servers = None
    _cold(db)
    assert rel_manager.get_mutual_servers_bulk(u1.id, [u2.id, u3.id]) == {
        u2.id: [],
        u3.id: [],
    }


@pytest.mark.relationships
def test_bulk_lookups_raise_on_db_failure(db, rel_manager, two_users):
    """A failed authoritative lookup must raise so callers can deny."""
    u1, u2 = two_users
    rel_manager._servers = object()
    _cold(db)
    with patch.object(db, "fetch_all", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            rel_manager.get_friend_ids_bulk([u1.id])
        with pytest.raises(RuntimeError):
            rel_manager.get_mutual_servers_bulk(u1.id, [u2.id])


# --- gateway hot path --------------------------------------------------------


@pytest.mark.relationships
def test_gateway_dm_audience_uses_one_bulk_lookup(
    db, rel_manager, three_users, monkeypatch
):
    from src.core.events.gateway_emit import _dm_participants_without_blocks

    u1, u2, u3 = three_users
    rel_manager.block_user(u2.id, u1.id)  # u2 blocked the actor
    participants = [u1.id, u2.id, u3.id]
    monkeypatch.setattr("src.api.get_relationships", lambda: rel_manager)

    _cold(db)
    per_id = [set(rel_manager.get_all_blocked_ids(p)) for p in participants]
    old_count = db.get_request_metrics()["query_count"]

    _cold(db)
    with (
        patch.object(
            rel_manager,
            "get_all_blocked_ids_bulk",
            wraps=rel_manager.get_all_blocked_ids_bulk,
        ) as bulk,
        patch.object(
            rel_manager,
            "get_all_blocked_ids",
            wraps=rel_manager.get_all_blocked_ids,
        ) as single,
    ):
        result = _dm_participants_without_blocks(participants, u1.id)
    new_count = db.get_request_metrics()["query_count"]

    bulk.assert_called_once_with(participants)
    single.assert_not_called()
    assert any(u1.id in blocked for blocked in per_id)
    assert u2.id not in result
    assert u1.id in result
    assert u3.id in result
    assert new_count < old_count
    assert new_count == 1


@pytest.mark.relationships
def test_gateway_dm_audience_fails_closed_when_bulk_lookup_fails(monkeypatch):
    from src.core.events.gateway_emit import _dm_participants_without_blocks

    class FailingRelationships:
        def get_all_blocked_ids_bulk(self, _ids):
            raise RuntimeError("authoritative lookup unavailable")

    monkeypatch.setattr("src.api.get_relationships", lambda: FailingRelationships())
    assert _dm_participants_without_blocks([11, 22, 33], 11) == []
