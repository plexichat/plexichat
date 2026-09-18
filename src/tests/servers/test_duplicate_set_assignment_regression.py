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
written by every statement. The SQL-level assertion lives in
``_conftest/sql_guard.py`` and is applied to every per-column builder by
``test_update_builder_sql_guard.py``; these tests pin the two builders that
actually carried the defect.
"""

import time

from src.tests._conftest.sql_guard import (
    assert_no_duplicate_assignments,
    capture_executed_sql,
    update_statements,
)


def test_update_role_never_assigns_a_column_twice(server_with_members):
    """A rename must emit only single-assignment UPDATEs."""
    server, owner, _, _, _, admin_role, servers = server_with_members

    statements = capture_executed_sql(
        servers._db,
        lambda: servers.update_role(
            user_id=owner.id, role_id=admin_role.id, name="Super Admin"
        ),
    )

    assert update_statements(statements), "expected update_role to issue UPDATEs"
    assert_no_duplicate_assignments(statements)


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

    statements = capture_executed_sql(
        servers._db,
        lambda: servers.update_scheduled_event(
            user_id=owner.id, event_id=event.id, name="Meetup 2"
        ),
    )

    assert update_statements(statements), "expected update_scheduled_event UPDATEs"
    assert_no_duplicate_assignments(statements)
