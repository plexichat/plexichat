"""Regression: bulk presence reads replace per-member queries with bounded batches.

The guild/roster and fan-out paths can ask for presence for an arbitrary number
of members. Resolving that list with one ``get_presence`` call per member costs
several DB round trips per user (an expired-status cleanup plus the joined
select) and builds an ``IN (...)`` statement that grows without bound. The
bulk path must instead issue a constant number of statements per 500 ids, with
each ``IN (...)`` clause binding at most 500 user ids.

These tests measure the query count directly through request metrics (the same
way the other N+1 regressions in this repo are pinned) and assert both the
reduction and the 500-id bound.
"""

import math
from unittest.mock import patch

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern
from src.core.presence.models import UserStatus

pytestmark = pytest.mark.presence

_CHUNK = 500


def _cold(db):
    """Start from a cold cache so the measurement cannot hide extra queries."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


def _seed_presence(db, user_ids):
    """Insert presence rows directly (bulk reads do not require auth users)."""
    now = 1700000000000
    db.execute_many(
        "INSERT OR REPLACE INTO pres_presence (user_id, status, last_seen, updated_at) VALUES (?, ?, ?, ?)",
        [(uid, UserStatus.ONLINE.value, now, now) for uid in user_ids],
    )


def _count_queries(db):
    return db.get_request_metrics()["query_count"]


def test_bulk_presence_replaces_per_member_queries(db, presence_manager):
    ids = list(range(1, 41))
    _seed_presence(db, ids)

    # BEFORE: the naive per-member loop (one get_presence per id, cache forced
    # cold on purpose so the DB work is visible in request metrics).
    _cold(db)
    before = {}
    for uid in ids:
        before[uid] = presence_manager.get_presence(uid, use_cache=False)
    per_member_queries = _count_queries(db)

    # AFTER: one bulk call, chunked into bounded IN (...) statements.
    _cold(db)
    after = presence_manager.get_presences(ids)
    bulk_queries = _count_queries(db)

    assert [p.user_id for p in after] == ids
    assert {p.user_id: p.status for p in after} == {
        uid: UserStatus.ONLINE for uid in ids
    }
    # Result parity with the replaced loop.
    assert {p.user_id: p.status for p in after} == {
        uid: p.status for uid, p in before.items()
    }

    # One chunk, four statements (presence/custom-status cleanup/custom
    # status/activity) instead of two statements per member.
    assert per_member_queries >= len(ids)
    assert bulk_queries <= 6, f"bulk presence issued {bulk_queries} queries"
    assert bulk_queries < per_member_queries, (
        f"expected fewer queries than the {per_member_queries}-query per-member "
        f"loop, got {bulk_queries}"
    )


def test_bulk_presence_in_clauses_are_bounded_to_500(db, presence_manager):
    ids = list(range(1, 1102))  # 1101 ids -> ceil(1101 / 500) == 3 chunks
    _seed_presence(db, ids)
    _cold(db)

    with (
        patch.object(db, "fetch_all", wraps=db.fetch_all) as fetch_all,
        patch.object(db, "execute", wraps=db.execute) as execute,
    ):
        result = presence_manager.get_presences(ids)

    assert len(result) == len(ids)
    assert [p.user_id for p in result] == ids
    assert all(p.status == UserStatus.ONLINE for p in result)

    # ``fetch_all`` routes through ``execute`` internally, so inspect each
    # call site separately rather than summing the two recorders.
    select_sqls = [
        call.args[0]
        for call in fetch_all.call_args_list
        if isinstance(call.args[0], str) and " IN (" in call.args[0]
    ]
    cleanup_sqls = [
        call.args[0]
        for call in execute.call_args_list
        if isinstance(call.args[0], str)
        and "DELETE FROM pres_custom_status" in call.args[0]
    ]
    chunks = math.ceil(len(ids) / _CHUNK)
    # Three SELECT statements plus one cleanup statement per chunk.
    assert len(select_sqls) == 3 * chunks
    assert len(cleanup_sqls) == chunks
    for sql in select_sqls + cleanup_sqls:
        placeholders = sql.count("?")
        assert placeholders <= _CHUNK + 1, (
            f"IN clause bound {placeholders} variables: {sql.strip()}"
        )

    # Constant-query scaling: the bulk read costs one bounded batch per 500
    # ids, never one query per member.
    expected_queries = 4 * chunks
    assert _count_queries(db) == expected_queries
