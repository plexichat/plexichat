"""Thread detail hydration must not re-read the row it already loaded.

``ThreadManager.get_thread`` loaded the ``thread_threads`` row once, then called
``can_view_thread(user_id, thread_id)``, which loaded the *same* row again via
``_get_thread_internal`` only to decide visibility from fields already in hand.
Every ``GET /threads/{id}`` (and every create/archive/lock path that re-reads a
thread through ``get_thread``) therefore paid two point reads plus a duplicated
name decrypt where one was enough.

The visibility decision now takes the already-loaded row, mirroring the listing
filter fix but on the detail path: same authorization, half the point reads.
This test pins the read count and the visibility outcome.
"""

from unittest.mock import Mock

import pytest

from src.core.threads.manager import ThreadManager
from src.core.threads.models import AutoArchiveDuration, ThreadState, ThreadType

pytestmark = pytest.mark.unit

_VIEWER_ID = 42
_FUTURE = 9_999_999_999_999


def _row(thread_id, thread_type=ThreadType.PUBLIC.value, state=ThreadState.ACTIVE):
    return {
        "id": thread_id,
        "channel_id": 1,
        "server_id": 5,
        "owner_id": 7,
        "name": "thread",
        "name_encrypted": None,
        "thread_type": thread_type,
        "state": state.value,
        "parent_message_id": None,
        "auto_archive_duration": AutoArchiveDuration.SEVEN_DAYS.value,
        "message_count": 0,
        "member_count": 1,
        "created_at": _FUTURE,
        "archived_at": None,
        "last_message_at": _FUTURE,
        "conversation_id": None,
        "locked": 0,
    }


class _CountingDB:
    """DB double that counts point reads of ``thread_threads``."""

    def __init__(self, rows):
        self.rows = rows
        self.thread_reads = []

    def fetch_one(self, sql, params=()):
        if "FROM thread_threads" in sql and "id = ?" in sql:
            self.thread_reads.append((sql, params))
            for row in self.rows:
                if row["id"] == params[0]:
                    return dict(row)
            return None
        return None

    def fetch_all(self, sql, params=()):
        return []

    def execute(self, sql, params=()):
        pass


def _manager(rows, grant=True, member=True):
    db = _CountingDB(rows)
    mgr = ThreadManager(db)
    servers = Mock()
    servers.has_permission = Mock(return_value=grant)
    mgr._servers = servers
    mgr._is_member = Mock(return_value=member)
    mgr._is_server_member = Mock(return_value=member)
    return mgr, db, servers


def test_get_thread_reads_row_once_for_public_visible():
    mgr, db, servers = _manager([_row(100)])

    thread = mgr.get_thread(_VIEWER_ID, 100)

    assert thread is not None and thread.id == 100
    # Before: two `SELECT * FROM thread_threads WHERE id = ?` reads.
    # After: the loaded row is reused for the visibility decision.
    assert len(db.thread_reads) == 1, (
        "get_thread re-read the thread row for visibility: "
        f"{len(db.thread_reads)} point reads"
    )
    assert servers.has_permission.call_count == 1
    assert servers.has_permission.call_args.args == (
        _VIEWER_ID,
        5,
        "channels.view",
        1,
    )


def test_get_thread_reads_row_once_for_private_member():
    mgr, db, servers = _manager(
        [_row(200, thread_type=ThreadType.PRIVATE.value)], member=True
    )

    thread = mgr.get_thread(_VIEWER_ID, 200)

    assert thread is not None and thread.id == 200
    assert len(db.thread_reads) == 1
    # Private visibility is membership-based; no channel permission check.
    assert servers.has_permission.call_count == 0


def test_visibility_still_enforced_without_the_refetch():
    public_denied, _, _ = _manager([_row(300)], grant=False)
    assert public_denied.get_thread(_VIEWER_ID, 300) is None

    private_nonmember, _, _ = _manager(
        [_row(400, thread_type=ThreadType.PRIVATE.value)], member=False
    )
    assert private_nonmember.get_thread(_VIEWER_ID, 400) is None
