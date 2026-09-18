"""Regression: per-column UPDATEs must not assign the same column twice.

``update_role`` and ``update_scheduled_event`` execute one
``UPDATE ... SET <column> = ?, updated_at = ?`` statement per changed column,
and both appended an explicit ``updated_at = ?`` entry to their ``updates``
list too. The generic loop therefore emitted
``SET updated_at = ?, updated_at = ?`` for that entry. SQLite tolerates the
duplicate (the last assignment wins), but PostgreSQL rejects it with
``multiple assignments to same column "updated_at"`` -- so every role or event
update failed outright on a Postgres deployment.

The fix drops the redundant ``updated_at`` entry; the timestamp is already
written by every statement. These tests capture the SQL actually executed and
assert no statement assigns a column more than once, so the regression is
caught on any backend (including the SQLite-only test run).
"""

import re
import time

_SET_CLAUSE = re.compile(r"\bSET\b(.*?)(?:\bWHERE\b|$)", re.IGNORECASE | re.DOTALL)


def _duplicate_set_columns(sql: str) -> list:
    """Return columns assigned more than once in an UPDATE's SET clause."""
    match = _SET_CLAUSE.search(sql)
    if not match:
        return []
    seen = set()
    duplicates = []
    for assignment in match.group(1).split(","):
        column = assignment.split("=", 1)[0].strip().lower()
        if not column:
            continue
        if column in seen and column not in duplicates:
            duplicates.append(column)
        seen.add(column)
    return duplicates


def _capture_update_statements(executor, action) -> list:
    """Run ``action`` and return the UPDATE statements it executed."""
    statements = []
    original = executor.execute

    def _record(sql, params=None, *args, **kwargs):
        statements.append(str(sql))
        return original(sql, params, *args, **kwargs)

    executor.execute = _record
    try:
        action()
    finally:
        executor.execute = original
    return [sql for sql in statements if sql.lstrip().upper().startswith("UPDATE")]


def test_update_role_never_assigns_a_column_twice(server_with_members):
    """A rename must emit only single-assignment UPDATEs."""
    server, owner, _, _, _, admin_role, servers = server_with_members

    statements = _capture_update_statements(
        servers._db,
        lambda: servers.update_role(
            user_id=owner.id, role_id=admin_role.id, name="Super Admin"
        ),
    )

    assert statements, "expected update_role to issue UPDATE statements"
    for sql in statements:
        assert _duplicate_set_columns(sql) == [], sql


def test_update_scheduled_event_never_assigns_a_column_twice(server_with_members):
    """A rename must not emit a duplicate ``updated_at`` assignment either."""
    server, owner, *_rest, servers = server_with_members

    event = servers.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name="Meetup",
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=servers.ScheduledEventType.EXTERNAL,
        location="Central Park",
    )

    statements = _capture_update_statements(
        servers._db,
        lambda: servers.update_scheduled_event(
            user_id=owner.id, event_id=event.id, name="Meetup 2"
        ),
    )

    assert statements, "expected update_scheduled_event to issue UPDATE statements"
    for sql in statements:
        assert _duplicate_set_columns(sql) == [], sql
