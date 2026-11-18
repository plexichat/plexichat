"""Regressions for the batched two-way block lookup.

DM event routing resolves the two-way block set for every conversation
participant. Historically that was one cached lookup per participant - an
N+1 fan-out on cold caches (and a burst of Valkey GETs even when warm). The
relationship manager now exposes ``get_all_blocked_ids_bulk``, which reads the
per-user sets in one cache MGET and, on a miss, a bounded number of chunked
``IN`` queries (<= 500 ids per side of the predicate).

These tests pin three properties:
* result parity with the per-user lookup, including fail-closed handling of
  unknown ids (empty set, never "unblocked by fiat");
* a query-count reduction from N per-id queries to one chunked query;
* hard bounds on the number of IN binds (no unbounded IN clause).

The event router is also checked to use the single bulk call and to fail
closed (deliver to nobody) when the authoritative block lookup is unavailable.
"""

from unittest.mock import patch

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern


def _cold(db):
    """Start from cold caches so the measurement cannot hide extra queries."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


@pytest.mark.relationships
def test_bulk_matches_per_user_and_cuts_queries(db, rel_manager, three_users):
    user1, user2, user3 = three_users
    rel_manager.block_user(user1.id, user2.id)
    rel_manager.block_user(user3.id, user1.id)

    # Real ids with real block rows, plus synthetic ids with no rows at all:
    # the latter must resolve to an empty set (fail closed), never be omitted.
    ids = [user1.id, user2.id, user3.id] + [900000 + i for i in range(9)]

    _cold(db)
    per_user = {uid: set(rel_manager.get_all_blocked_ids(uid)) for uid in ids}
    old_count = db.get_request_metrics()["query_count"]

    _cold(db)
    bulk = rel_manager.get_all_blocked_ids_bulk(ids)
    new_count = db.get_request_metrics()["query_count"]

    assert {uid: set(values) for uid, values in bulk.items()} == per_user
    assert set(bulk) == set(ids)
    # The old pattern issues one query per id; the batched path issues one.
    assert old_count >= len(ids)
    assert new_count == 1
    assert new_count < old_count


@pytest.mark.relationships
def test_bulk_empty_input_issues_no_queries(db, rel_manager):
    _cold(db)
    assert rel_manager.get_all_blocked_ids_bulk([]) == {}
    assert db.get_request_metrics()["query_count"] == 0


@pytest.mark.relationships
def test_bulk_chunks_in_binds_at_500(db, rel_manager):
    ids = list(range(1, 1201))  # 1200 ids -> chunks of 500, 500, 200
    captured = []
    original_fetch_all = db.fetch_all

    def spy(query, params=None):
        captured.append((query, params))
        return original_fetch_all(query, params)

    _cold(db)
    with patch.object(db, "fetch_all", side_effect=spy):
        result = rel_manager.get_all_blocked_ids_bulk(ids)

    assert set(result) == set(ids)
    assert len(captured) == 3, "1200 ids must resolve in exactly 3 chunks"

    chunk_sizes = []
    for query, params in captured:
        assert params is not None
        # One bind per placeholder, and the query has two IN clauses.
        assert query.count("?") == len(params)
        assert len(params) % 2 == 0
        assert query.count("?") <= 1000  # <= 500 per IN side
        chunk_sizes.append(len(params) // 2)
    assert sorted(chunk_sizes) == [200, 500, 500]


class _StubMessaging:
    def __init__(self, participant_ids):
        self._participant_ids = participant_ids

    def get_participant_ids(self, _conversation_id):
        return list(self._participant_ids)


@pytest.mark.relationships
def test_router_uses_one_bulk_lookup(db, rel_manager, three_users):
    from src.core.events.models import Event
    from src.core.events.router import EventRouter
    from src.core.events.types import EventType

    user1, user2, user3 = three_users
    rel_manager.block_user(user1.id, user2.id)
    participants = [user1.id, user2.id, user3.id]

    router = EventRouter(
        relationships_module=rel_manager,
        messaging_module=_StubMessaging(participants),
    )
    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={},
        channel_id=4242,
        user_id=user1.id,
    )

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
        recipients = router.get_recipients(event)

    bulk.assert_called_once_with(participants)
    single.assert_not_called()
    # Same exclusion as the old per-id path: the blocked counterpart never
    # receives the event; the actor and unrelated participant still do.
    assert user2.id not in recipients
    assert user1.id in recipients
    assert user3.id in recipients


@pytest.mark.relationships
def test_router_fails_closed_when_bulk_lookup_fails():
    from src.core.events.models import Event
    from src.core.events.router import EventRouter
    from src.core.events.types import EventType

    class FailingRelationships:
        def get_all_blocked_ids_bulk(self, _user_ids):
            raise RuntimeError("authoritative lookup unavailable")

    router = EventRouter(
        relationships_module=FailingRelationships(),
        messaging_module=_StubMessaging([11, 22]),
    )
    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={},
        channel_id=4242,
        user_id=11,
    )

    # A failed block lookup must deliver to nobody, never leak to a possibly
    # blocked pair (the old code ``continue``d and left the audience open).
    assert router.get_recipients(event) == []


@pytest.mark.relationships
def test_router_legacy_still_filters_via_per_id_lookup():
    """Lightweight stubs without the bulk helper keep the old behaviour."""
    from src.core.events.models import Event
    from src.core.events.router import EventRouter
    from src.core.events.types import EventType

    class LegacyRelationships:
        def get_all_blocked_ids(self, user_id):
            return [22] if user_id == 11 else []

    router = EventRouter(
        relationships_module=LegacyRelationships(),
        messaging_module=_StubMessaging([11, 22, 33]),
    )
    event = Event(
        event_type=EventType.MESSAGE_CREATE,
        data={},
        channel_id=4242,
        user_id=11,
    )

    assert 22 not in router.get_recipients(event)
