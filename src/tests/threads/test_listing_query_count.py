"""Channel thread listings resolve private membership in a single query.

BEFORE this change the active/archived listings issued one ``fetch_all`` for
the threads and a second ``fetch_all`` against ``thread_members`` whenever any
private thread was present. The membership marker is now folded into the
index-backed listing query via a LEFT JOIN, so the query count is constant
regardless of how many private threads exist. Visibility rules are unchanged:
public threads need ``channels.view``; private threads need membership.
"""

from unittest.mock import Mock

import pytest

from src.core.threads.manager import ThreadManager
from src.core.threads.models import AutoArchiveDuration, ThreadState, ThreadType

pytestmark = pytest.mark.unit

_FUTURE = 9_999_999_999_999


def _thread_row(
    thread_id,
    thread_type,
    membership_user_id,
    state=ThreadState.ACTIVE,
    last_message_at=_FUTURE,
    duration=AutoArchiveDuration.SEVEN_DAYS.value,
):
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
        "auto_archive_duration": duration,
        "message_count": 0,
        "member_count": 1,
        "created_at": _FUTURE,
        "archived_at": None,
        "last_message_at": last_message_at,
        "conversation_id": None,
        "locked": 0,
        "membership_user_id": membership_user_id,
    }


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_all_calls = []
        self.executed = []

    def fetch_one(self, sql, params=()):
        if "srv_channels" in sql:
            return {"id": 1, "server_id": 5, "deleted": 0}
        if "srv_members" in sql:
            # Viewer is a live member of the backing server; private threads
            # now require this roster row in addition to thread membership.
            return {"1": 1}
        return None

    def fetch_all(self, sql, params=()):
        self.fetch_all_calls.append((sql, params))
        return self.rows

    def execute(self, sql, params=()):
        self.executed.append((sql, params))


def _manager(rows, grant=True):
    db = _FakeDB(rows)
    mgr = ThreadManager(db)
    servers = Mock()
    servers.has_permission = Mock(return_value=grant)
    mgr._servers = servers
    return mgr, db


def test_active_listing_uses_one_query_with_membership_join():
    rows = [
        _thread_row(101, ThreadType.PRIVATE.value, 42),
        _thread_row(102, ThreadType.PRIVATE.value, None),
        _thread_row(103, ThreadType.PUBLIC.value, None),
    ]
    mgr, db = _manager(rows)

    result = mgr.get_active_threads(42, 1)

    assert [t.id for t in result] == [101, 103]
    assert len(db.fetch_all_calls) == 1
    sql, params = db.fetch_all_calls[0]
    assert "LEFT JOIN thread_members" in sql
    assert params == (42, 1, ThreadState.ACTIVE.value)


def test_archived_listing_uses_one_query_with_membership_join():
    rows = [
        _thread_row(201, ThreadType.PRIVATE.value, 42, ThreadState.ARCHIVED),
        _thread_row(202, ThreadType.PUBLIC.value, None, ThreadState.ARCHIVED),
    ]
    mgr, db = _manager(rows)

    result = mgr.get_archived_threads(42, 1)

    assert [t.id for t in result] == [201, 202]
    assert len(db.fetch_all_calls) == 1
    sql, params = db.fetch_all_calls[0]
    assert "LEFT JOIN thread_members" in sql
    assert params == (42, 1, ThreadState.ARCHIVED.value, 50)


def test_archived_before_cursor_stays_one_query():
    mgr, db = _manager([])

    mgr.get_archived_threads(42, 1, limit=25, before_timestamp=9_000_000_000_000)

    assert len(db.fetch_all_calls) == 1
    _sql, params = db.fetch_all_calls[0]
    assert params == (42, 1, ThreadState.ARCHIVED.value, 9_000_000_000_000, 25)


def test_auto_archive_updates_are_batched_into_one_write():
    rows = [
        _thread_row(301, ThreadType.PUBLIC.value, None, last_message_at=1, duration=60),
        _thread_row(302, ThreadType.PUBLIC.value, None, last_message_at=1, duration=60),
        _thread_row(303, ThreadType.PUBLIC.value, None, last_message_at=1, duration=60),
    ]
    mgr, db = _manager(rows)

    result = mgr.get_active_threads(42, 1)

    assert result == []
    assert len(db.executed) == 1
    sql, params = db.executed[0]
    assert "UPDATE thread_threads SET state = ?" in sql
    assert params[0] == ThreadState.ARCHIVED.value
    assert set(params[2:]) == {301, 302, 303}


def test_no_view_permission_still_hides_public_threads():
    rows = [
        _thread_row(101, ThreadType.PRIVATE.value, 42),
        _thread_row(103, ThreadType.PUBLIC.value, None),
    ]
    mgr, _db = _manager(rows, grant=False)

    result = mgr.get_active_threads(42, 1)

    assert [t.id for t in result] == [101]
