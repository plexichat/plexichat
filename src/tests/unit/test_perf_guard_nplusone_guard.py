"""Fail when a hot read path starts issuing an unbatched per-item DB call.

A new FastAPI dependency (or a refactor inside an existing one) that resolves
related rows one at a time turns a constant-query read into an N+1.  Total
latency barely moves for a handful of rows, so a wall-clock assert misses it;
the query count, however, grows linearly with the number of rows.

These tests run the same read path twice with very different row counts and
assert the query count is *constant*.  Adding any unbatched DB call that runs
per message/per member breaks the invariant immediately and deterministically.

Run with: pytest -m performance -n 0 src/tests/unit/test_perf_guard_nplusone_guard.py
"""

import uuid

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern

pytestmark = pytest.mark.performance


def _queries(db, call):
    """Run ``call`` from a cold cache and return its statement count."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    result = call()
    return db.get_request_metrics()["query_count"], result


def test_message_list_query_count_does_not_scale_with_messages(
    db, messaging_manager, test_dm
):
    """Listing 20 messages must cost the same queries as listing one."""
    dm, user, _ = test_dm

    for i in range(1):
        messaging_manager.send_message(
            user_id=user.id, conversation_id=dm.id, content=f"one-{i}"
        )
    one_queries, one = _queries(
        db,
        lambda: messaging_manager.get_messages(
            user_id=user.id, conversation_id=dm.id, limit=50
        ),
    )
    assert len(one) == 1

    for i in range(19):
        messaging_manager.send_message(
            user_id=user.id, conversation_id=dm.id, content=f"many-{i}"
        )
    many_queries, many = _queries(
        db,
        lambda: messaging_manager.get_messages(
            user_id=user.id, conversation_id=dm.id, limit=50
        ),
    )
    assert len(many) == 20

    assert many_queries <= one_queries + 2, (
        "message listing introduced an unbatched per-message DB call: "
        f"1 message -> {one_queries} queries, 20 messages -> {many_queries}"
    )


def test_member_list_query_count_does_not_scale_with_members(
    db, auth_manager, server_manager, test_server
):
    """Listing 20 members must cost the same queries as listing one."""
    server, owner = test_server
    server_id = server.id

    def add_members(count):
        for i in range(count):
            tag = uuid.uuid4().hex[:10]
            member = auth_manager.register(
                username=f"pg_{tag}",
                email=f"pg_{tag}@example.test",
                password="PerfGuard123!",
            )
            server_manager.add_member(server_id, member.id)

    add_members(1)
    one_queries, one = _queries(
        db, lambda: server_manager.get_members(owner.id, server_id)
    )
    assert len(one) == 2  # owner + the single added member

    add_members(19)
    many_queries, many = _queries(
        db, lambda: server_manager.get_members(owner.id, server_id)
    )
    assert len(many) == 21

    assert many_queries <= one_queries + 2, (
        "member listing introduced an unbatched per-member DB call: "
        f"2 members -> {one_queries} queries, 21 members -> {many_queries}"
    )
