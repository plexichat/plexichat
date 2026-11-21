"""Regression: role-mention fan-out hydrates members in ONE batched query.

Before the fix, ``create_notifications`` called ``get_role_members(db, role)``
once per valid role mention. Each call ran

    SELECT m.user_id FROM srv_member_roles mr
    JOIN srv_members m ON mr.member_id = m.id
    WHERE mr.role_id = ?

so a message mentioning N roles paid N round trips for the same table/join with
only the bound role id changing - a textbook N+1 on the hot notification
fan-out. ``get_role_members_bulk`` now resolves every distinct role id with a
single ``WHERE mr.role_id IN (...)`` query plus an in-memory map.

The counters below prove both sides: the per-row reference issues exactly N
queries (growing with N) while the batched primitive issues exactly one for any
N below the bind cap, with byte-identical role -> member output. A scope
control verifies that members of an *unmentioned* role never enter the map and
that only the requested ids are bound.
"""

import pytest

from src.core.notifications.manager.creator import (
    get_role_members_bulk,
)
from src.core.notifications.manager.helpers import get_role_members
from src.core.notifications.models import Mention, MentionType


class _CountingDB:
    """Fake DB that counts per-role vs batched ``srv_member_roles`` reads."""

    def __init__(self, members_by_role):
        self.members_by_role = {int(r): list(m) for r, m in members_by_role.items()}
        self.per_row_calls = []
        self.batched_calls = []
        self.batched_params = []

    def fetch_all(self, sql, params=()):
        if "srv_member_roles" not in sql:
            return []
        if "IN (" in sql:
            self.batched_calls.append(sql)
            self.batched_params.append(tuple(params))
            rows = []
            for rid in params:
                for uid in self.members_by_role.get(int(rid), []):
                    rows.append({"role_id": rid, "user_id": uid})
            return rows
        # Per-row form used by get_role_members(): WHERE mr.role_id = ?
        self.per_row_calls.append(tuple(params))
        return [
            {"user_id": uid} for uid in self.members_by_role.get(int(params[0]), [])
        ]


def _many_roles(n):
    # Distinct role ids and distinct member ids keep the map unambiguous.
    return {1000 + i * 10: [5000 + i * 10 + j for j in range(3)] for i in range(n)}


@pytest.mark.notifications
@pytest.mark.parametrize("n", [1, 5, 40])
def test_batched_matches_per_row_output_with_constant_query_count(n):
    """Identical output; per-row cost grows with N, batched cost stays at 1."""
    db = _CountingDB(_many_roles(n))
    role_ids = list(db.members_by_role)

    # BEFORE: the exact per-role primitive the fan-out used to call in a loop.
    expected = {rid: get_role_members(db, rid) for rid in role_ids}
    assert len(db.per_row_calls) == n  # N queries, one per role

    db.per_row_calls.clear()
    db.batched_calls.clear()

    # AFTER: one set-based query for the whole fan-out.
    actual = get_role_members_bulk(db, role_ids)

    assert actual == expected  # identical role -> members output
    assert len(db.per_row_calls) == 0
    assert len(db.batched_calls) == 1  # constant, independent of n
    assert db.batched_params == [tuple(role_ids)]  # every id bound exactly once


@pytest.mark.notifications
def test_scope_preservation_only_requested_roles_are_loaded():
    """An unmentioned role's members cannot leak into the map (no widening)."""
    members = {
        1: [11, 12],
        2: [21],
        999: [9991, 9992],  # exists in DB but is NOT mentioned
    }
    db = _CountingDB(members)

    result = get_role_members_bulk(db, [1, 2])

    assert result == {1: [11, 12], 2: [21]}
    assert 999 not in result
    # Members of the unmentioned role never appear under any requested role.
    notified = {uid for members in result.values() for uid in members}
    assert notified.isdisjoint({9991, 9992})
    assert set(db.batched_params[0]) == {1, 2}  # predicate bound to requested ids only


@pytest.mark.notifications
def test_duplicate_and_malformed_ids_are_not_bound():
    """Repeated ids collapse; non-integer ids are skipped like the old probe."""
    db = _CountingDB({7: [70, 71]})
    result = get_role_members_bulk(db, [7, "7", 7, "not-an-int", None])

    assert result == {7: [70, 71]}
    assert len(db.batched_calls) == 1
    assert db.batched_params == [(7,)]


class _DbQueryCounter:
    """Counts ``srv_member_roles`` reads without changing DB behaviour."""

    def __init__(self, db):
        self._db = db
        self.batched = 0
        self.per_row = 0

    def fetch_all(self, sql, params=()):
        if "srv_member_roles" in sql:
            if "IN (" in sql:
                self.batched += 1
            else:
                self.per_row += 1
        return self._db.fetch_all(sql, params)

    def __getattr__(self, name):
        return getattr(self._db, name)


@pytest.mark.notifications
def test_hot_fanout_uses_one_role_member_query(
    notification_manager, modules, user_pool, monkeypatch
):
    """The real ``create_notifications`` path no longer loops per role."""
    author = user_pool.get_user()
    members = [user_pool.get_user() for _ in range(5)]
    server = modules.servers.create_server(author.id, "batch server")

    roles = []
    for i, member in enumerate(members):
        role = modules.servers.create_role(
            user_id=author.id,
            server_id=server.id,
            name=f"batch_role_{i}",
            permissions={},
            mentionable=True,
        )
        modules.servers.add_member(server.id, member.id)
        modules.servers.assign_role(author.id, server.id, member.id, role.id)
        roles.append(role)

    mentions = [
        Mention(MentionType.ROLE, role.id, f"<@&{role.id}>", 0, 12) for role in roles
    ]
    monkeypatch.setattr(
        notification_manager, "parse_mentions", lambda content: mentions
    )
    monkeypatch.setattr(
        notification_manager, "validate_mentions", lambda *a, **kw: mentions
    )

    captured = {}

    def _fake_bulk(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(notification_manager, "create_notifications_bulk", _fake_bulk)
    counter = _DbQueryCounter(notification_manager._db)
    monkeypatch.setattr(notification_manager, "_db", counter)

    notification_manager.create_notifications(
        message_id=1,
        author_id=author.id,
        conversation_id=1,
        content="ignored",
        server_id=server.id,
        channel_id=None,
    )

    # Same recipients as the old per-role fan-out, one batched read, zero
    # per-role reads (the N+1 is gone from the hot path).
    assert set(captured["user_ids"]) == {m.id for m in members}
    assert counter.batched == 1
    assert counter.per_row == 0
