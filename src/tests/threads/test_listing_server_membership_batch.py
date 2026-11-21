"""Private-thread listings must resolve live server membership in one query.

The live-``srv_members`` gate added to the private branches of
``get_user_threads`` / ``get_user_private_threads`` (``_filter_user_threads``)
and ``get_active_threads`` / ``get_archived_threads`` used to call
``_is_server_member`` once per listed private thread.  Each call is a separate
``SELECT 1 FROM srv_members WHERE server_id = ? AND user_id = ?`` point read,
so a listing of N private threads issued N roster queries -- an N+1 that is
especially painful on the unpaginated ``/users/@me/threads`` listing.

The gate now resolves every backing server's live-roster membership with a
single batched ``SELECT server_id FROM srv_members WHERE user_id = ?`` and a
set lookup.  Fail-closed semantics are preserved: no row for a server means
no grant, and a failed lookup yields an empty set (no private threads leak).
"""

from unittest.mock import Mock

import pytest

from src.core.threads.manager import ThreadManager
from src.core.threads.models import AutoArchiveDuration, ThreadState, ThreadType

pytestmark = pytest.mark.unit

_FUTURE = 9_999_999_999_999
_VIEWER_ID = 42
_SERVER_ID = 5
_CHANNEL_ID = 1
_THREAD_COUNT = 25


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
        "membership_user_id": _VIEWER_ID,
    }


class _CountingDB:
    """Counts every ``srv_members`` probe, point read or batched."""

    def __init__(self, rows, live=True):
        self.rows = rows
        self.live = live
        self.membership_queries = []

    def fetch_one(self, sql, params=()):
        if "srv_channels" in sql:
            return {"id": _CHANNEL_ID, "server_id": _SERVER_ID, "deleted": 0}
        if "srv_members" in sql:
            self.membership_queries.append(("fetch_one", sql, params))
            return {"1": 1} if self.live else None
        return None

    def fetch_all(self, sql, params=()):
        if "srv_members" in sql:
            self.membership_queries.append(("fetch_all", sql, params))
            return [{"server_id": _SERVER_ID}] if self.live else []
        return self.rows

    def execute(self, sql, params=()):
        pass


def _manager(rows, live=True):
    db = _CountingDB(rows, live=live)
    mgr = ThreadManager(db)
    servers = Mock()
    servers.has_permission = Mock(return_value=True)
    mgr._servers = servers
    return mgr, db


def _private_rows(state=ThreadState.ACTIVE):
    return [
        _row(10_000 + i, ThreadType.PRIVATE.value, state) for i in range(_THREAD_COUNT)
    ]


def _listings():
    return (
        ("user_threads", lambda mgr: mgr.get_user_threads(_VIEWER_ID)),
        ("user_private_threads", lambda mgr: mgr.get_user_private_threads(_VIEWER_ID)),
        ("active_threads", lambda mgr: mgr.get_active_threads(_VIEWER_ID, _CHANNEL_ID)),
        (
            "archived_threads",
            lambda mgr: mgr.get_archived_threads(_VIEWER_ID, _CHANNEL_ID),
        ),
    )


@pytest.mark.parametrize("name,call", _listings(), ids=[n for n, _ in _listings()])
def test_private_listing_membership_probe_is_batched(name, call):
    """N private threads cost a single roster query, not N point reads."""
    mgr, db = _manager(_private_rows())

    result = call(mgr)

    print(
        f"\nMEMBERSHIP_PROBE_COUNT {name} "
        f"threads={_THREAD_COUNT} queries={len(db.membership_queries)}"
    )
    assert len(result) == _THREAD_COUNT
    assert len(db.membership_queries) == 1, (
        f"{name} issued {len(db.membership_queries)} srv_members probes for "
        f"{_THREAD_COUNT} private threads (expected one batched query)"
    )
    kind, sql, _params = db.membership_queries[0]
    assert kind == "fetch_all"
    assert "SELECT server_id FROM srv_members WHERE user_id = ?" in sql


@pytest.mark.parametrize("name,call", _listings(), ids=[n for n, _ in _listings()])
def test_private_listing_fails_closed_without_live_roster(name, call):
    """No live roster row means no private threads, in every listing path."""
    mgr, db = _manager(_private_rows(), live=False)

    assert call(mgr) == []
    assert len(db.membership_queries) == 1
