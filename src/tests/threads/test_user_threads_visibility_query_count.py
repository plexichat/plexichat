"""``get_user_threads`` must not re-read every listed public thread.

The listing query already returns each thread row (with ``server_id`` and
``channel_id``) from ``thread_threads``.  The visibility filter then called
``can_view_thread`` for every public row, which re-fetches the *same* row via
``_get_thread_internal`` before evaluating ``channels.view``.  That is one
redundant ``thread_threads`` point-read per public thread -- an N+1 that grows
with the (unpaginated) "list my threads" result.

The filter now evaluates the very same ``channels.view`` check from the row it
already holds.  Authorization is unchanged: private threads stay membership
gated, public threads still need ``channels.view``, and a missing servers module
still fails closed.  This test pins the query count and the visibility outcome.
"""

from unittest.mock import Mock

import pytest

from src.core.threads.manager import ThreadManager
from src.core.threads.models import AutoArchiveDuration, ThreadState, ThreadType

pytestmark = pytest.mark.unit

_FUTURE = 9_999_999_999_999
_CHANNEL_ID = 1
_SERVER_ID = 5
_VIEWER_ID = 42


def _row(thread_id, thread_type, state=ThreadState.ACTIVE):
    return {
        "id": thread_id,
        "channel_id": _CHANNEL_ID,
        "server_id": _SERVER_ID,
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
    """Real-shaped DB double that counts point reads of ``thread_threads``."""

    def __init__(self, rows):
        self.rows = rows
        self.thread_refetch_sql = []

    def fetch_one(self, sql, params=()):
        if "thread_threads" in sql:
            self.thread_refetch_sql.append(sql)
            for row in self.rows:
                if row["id"] == params[0]:
                    return row
            return None
        if "srv_members" in sql:
            # Viewer is still a live member of the backing server (the
            # fail-closed roster probe added to private-thread listings).
            return {"1": 1}
        return None

    def fetch_all(self, sql, params=()):
        return self.rows

    def execute(self, sql, params=()):
        pass


def _manager(rows, grant=True):
    db = _CountingDB(rows)
    mgr = ThreadManager(db)
    servers = Mock()
    servers.has_permission = Mock(return_value=grant)
    mgr._servers = servers
    return mgr, db, servers


def _public_rows(count):
    return [_row(10_000 + i, ThreadType.PUBLIC.value) for i in range(count)]


def test_user_threads_does_not_refetch_listed_threads():
    """One listing query, zero redundant per-thread point reads."""
    rows = _public_rows(12)
    mgr, db, servers = _manager(rows)

    result = mgr.get_user_threads(_VIEWER_ID)

    assert len(result) == 12
    # Before the fix: one `SELECT * FROM thread_threads WHERE id = ?` per row.
    # After the fix: the listing row is reused, so none.
    assert db.thread_refetch_sql == [], (
        "get_user_threads re-read listed threads (N+1): "
        f"{len(db.thread_refetch_sql)} redundant thread_threads point reads"
    )
    # The channels.view decision is still made for every public thread, with
    # the same arguments the can_view_thread path used.
    assert servers.has_permission.call_count == 12
    for call in servers.has_permission.call_args_list:
        assert call.args == (_VIEWER_ID, _SERVER_ID, "channels.view", _CHANNEL_ID)


def test_user_threads_query_count_is_independent_of_page_size():
    two = _public_rows(2)
    many = _public_rows(50)
    mgr_two, db_two, _ = _manager(two)
    mgr_many, db_many, _ = _manager(many)

    two_result = mgr_two.get_user_threads(_VIEWER_ID)
    many_result = mgr_many.get_user_threads(_VIEWER_ID)

    print(
        "\nUSER_THREADS_REFETCH_COUNT "
        f"two={len(db_two.thread_refetch_sql)} fifty={len(db_many.thread_refetch_sql)}"
    )
    assert len(two_result) == 2
    assert len(many_result) == 50
    assert len(db_two.thread_refetch_sql) == len(db_many.thread_refetch_sql) == 0


def test_visibility_unchanged_for_public_and_private():
    """Private threads pass on membership; public ones need channels.view."""
    rows = [
        _row(20_001, ThreadType.PRIVATE.value),
        _row(20_002, ThreadType.PUBLIC.value),
    ]
    deny_mgr, _, servers = _manager(rows, grant=False)
    deny_mgr._is_member = Mock(return_value=True)

    denied = deny_mgr.get_user_threads(_VIEWER_ID)
    assert [t.id for t in denied] == [20_001]
    assert servers.has_permission.call_count == 1

    allow_mgr, _, _ = _manager(rows, grant=True)
    allow_mgr._is_member = Mock(return_value=True)
    allowed = allow_mgr.get_user_threads(_VIEWER_ID)
    assert {t.id for t in allowed} == {20_001, 20_002}


def test_no_servers_module_still_fails_closed():
    """Without a servers module public threads are hidden, not leaked."""
    rows = [_row(30_001, ThreadType.PUBLIC.value)]
    mgr, _, _ = _manager(rows, grant=True)
    mgr._servers = None

    assert mgr.get_user_threads(_VIEWER_ID) == []
