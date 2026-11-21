"""Regression tests: ``get_thread_members`` must clamp an oversized ``limit``.

The manager only clamped from below (``max(0, limit)``) and forwarded ``limit``
straight into SQL ``LIMIT ?``. A caller-controlled large value (e.g.
``limit=100000000``) therefore returned the entire membership roster instead
of a single page -- an unbounded query/serialization. These tests seed more
members than the cap so the difference is observable, not vacuous: before the
fix an oversized limit returned every seeded row; after the fix it returns
exactly ``MAX_MEMBERS_PAGE_SIZE``.
"""

import pytest

from src.core.threads.manager.membership import MAX_MEMBERS_PAGE_SIZE


@pytest.fixture
def populated_thread(server_with_channel, modules):
    owner, _m1, _m2, _server, channel, _servers, threads = server_with_channel
    thread = threads.create_thread(owner.id, channel.id, "Limit clamp roster")
    db = modules._db
    # Seed more rows than the cap so an honored oversized limit is visible.
    seed_count = MAX_MEMBERS_PAGE_SIZE + 25
    base_user_id = 9_000_000
    for i in range(seed_count):
        db.execute(
            "INSERT INTO thread_members "
            "(thread_id, user_id, joined_at, last_read_message_id, muted) "
            "VALUES (?, ?, ?, NULL, 0)",
            (thread.id, base_user_id + i, 1_000 + i),
        )
    row = db.fetch_one(
        "SELECT COUNT(*) AS n FROM thread_members WHERE thread_id = ?", (thread.id,)
    )
    return threads, owner, thread, int(row["n"])


def test_oversized_limit_is_clamped(populated_thread):
    threads, owner, thread, stored = populated_thread
    # Non-vacuous: more members exist than the cap allows to be returned.
    assert stored > MAX_MEMBERS_PAGE_SIZE
    rows = threads.get_thread_members(owner.id, thread.id, limit=100_000_000)
    assert len(rows) == MAX_MEMBERS_PAGE_SIZE


def test_normal_limit_within_cap_is_unaffected(populated_thread):
    threads, owner, thread, _stored = populated_thread
    assert len(threads.get_thread_members(owner.id, thread.id, limit=25)) == 25
    assert (
        len(
            threads.get_thread_members(
                owner.id, thread.id, limit=MAX_MEMBERS_PAGE_SIZE
            )
        )
        == MAX_MEMBERS_PAGE_SIZE
    )


def test_invalid_and_negative_limits_fail_closed(populated_thread):
    threads, owner, thread, _stored = populated_thread
    assert threads.get_thread_members(owner.id, thread.id, limit=-1) == []
    assert threads.get_thread_members(owner.id, thread.id, limit=0) == []
    assert threads.get_thread_members(owner.id, thread.id, limit="not-an-int") == []
