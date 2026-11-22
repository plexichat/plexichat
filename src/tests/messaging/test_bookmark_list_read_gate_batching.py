"""PERF: bookmark list hydration must batch the per-row ``messages.read`` gate.

``BookmarkService.get_bookmarks`` already resolves message rows, pins and
attachments with bounded ``IN`` fan-outs, but it still called
``_conversation_read_allowed`` once per returned bookmark. Each call resolves
the backing channel and runs a ``messages.read`` permission check -- a DB/cache
round trip per row. A 200-row page of bookmarks in one channel paid 200
identical permission lookups.

The fix resolves the page's conversations once per distinct (strict-int)
conversation id in a bounded pass, keyed by the owner-scoped rows already
selected, and fails closed when a verdict cannot be proven.

Run with: pytest src/tests/messaging/test_bookmark_list_read_gate_batching.py -q -n 0
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.core.messaging.services.bookmarks import BookmarkService

pytestmark = pytest.mark.unit

USER = 777111
OTHER_USER = 777112
CHANNEL = 6000
OTHER_CHANNEL = 6001
SERVER = 7


class _DB:
    """Returns the owner-scoped bookmark page and records its one query."""

    def __init__(self, rows):
        self.rows = rows
        self.page_queries = []

    def fetch_all(self, query, params=None):
        if "FROM user_bookmarks" in query:
            self.page_queries.append(tuple(params or ()))
            return [dict(r) for r in self.rows]
        return []

    def fetch_one(self, query, params=None):
        return None


def _rows(n, conv_id=CHANNEL):
    return [
        {
            "id": 1000 + i,
            "user_id": USER,
            "message_id": 5000 + i,
            "conversation_id": conv_id,
            "label": None,
            "created_at": 1700000000000 + i,
        }
        for i in range(n)
    ]


class _Messaging:
    """Bulk resolver + participant gate; every message is viewable."""

    def __init__(self, n):
        self.messages = {
            5000 + i: SimpleNamespace(
                id=5000 + i,
                content=f"m{i}",
                author_id=42,
                created_at=1700000000001,
                conversation_id=CHANNEL,
            )
            for i in range(n)
        }
        self._participant_svc = SimpleNamespace(is_participant=lambda conv, uid: True)

    def get_messages_by_ids(self, user_id, ids):
        return {int(m): self.messages[int(m)] for m in ids if int(m) in self.messages}

    def get_message(self, user_id, mid):
        return self.messages.get(int(mid))


class _Servers:
    """Counts one ``messages.read`` check per channel resolution."""

    def __init__(
        self, *, messages_read=True, deny_channels=(), deny_users=(), raises=False
    ):
        self.messages_read = messages_read
        self.deny_channels = set(deny_channels)
        self.deny_users = set(deny_users)
        self.raises = raises
        self.permission_calls = []

    def channel_exists(self, channel_id):
        return channel_id in (CHANNEL, OTHER_CHANNEL)

    def get_channel_by_conversation(self, conversation_id):
        return None

    def get_channel(self, channel_id, user_id):
        return SimpleNamespace(server_id=SERVER)

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        if self.raises:
            raise RuntimeError("permission store down")
        if permission == "messages.read":
            return (
                self.messages_read
                and channel_id not in self.deny_channels
                and user_id not in self.deny_users
            )
        return True


def _run(n, servers, conv_id=CHANNEL, messaging=None, user_id=USER):
    db = _DB(_rows(n, conv_id))
    svc = BookmarkService(db, messaging if messaging is not None else _Messaging(n))
    with patch.object(api, "get_servers", lambda: servers):
        rows = svc.get_bookmarks(user_id)
    return rows, servers, db


def test_read_gate_query_count_is_flat_as_page_grows():
    counts = {}
    for n in (2, 20, 80):
        rows, servers, _ = _run(n, _Servers())
        assert len(rows) == n
        counts[n] = len(servers.permission_calls)

    # Before: one messages.read lookup per row (2, 20, 80) -- linear N+1.
    # After: one per distinct conversation id in the page.
    assert counts[2] == counts[20] == counts[80] == 1, counts


def test_distinct_conversations_resolved_once_each_and_denied_rows_dropped():
    rows = _rows(2, CHANNEL) + _rows(2, OTHER_CHANNEL)
    servers = _Servers(deny_channels={OTHER_CHANNEL})
    svc = BookmarkService(_DB(rows), _Messaging(4))
    with patch.object(api, "get_servers", lambda: servers):
        got = svc.get_bookmarks(USER)

    assert [r["message_id"] for r in got] == [5000, 5001]
    # Two conversations -> two checks, not four (one per row).
    assert len(servers.permission_calls) == 2, servers.permission_calls
    assert {c[-1] for c in servers.permission_calls} == {CHANNEL, OTHER_CHANNEL}


def test_unresolvable_read_gate_fails_closed():
    rows, servers, _ = _run(4, _Servers(raises=True))
    assert rows == []
    assert len(servers.permission_calls) >= 1


def test_strict_int_conversation_ids_are_not_bound_or_trusted():
    """A bool / non-numeric conversation id must never win a read verdict."""
    rows = _rows(2)
    rows[0]["conversation_id"] = True  # bool is NOT a snowflake
    rows[1]["conversation_id"] = "not-an-int"
    servers = _Servers()
    svc = BookmarkService(_DB(rows), _Messaging(2))
    with patch.object(api, "get_servers", lambda: servers):
        got = svc.get_bookmarks(USER)

    assert got == []
    assert all(c[-1] != True for c in servers.permission_calls)  # noqa: E712


def test_verdicts_are_owner_scoped():
    """The same conversation is gated separately for each credential."""
    servers = _Servers(deny_users={OTHER_USER})
    svc = BookmarkService(_DB(_rows(2)), _Messaging(2))
    with patch.object(api, "get_servers", lambda: servers):
        allowed = svc.get_bookmarks(USER)
        denied = svc.get_bookmarks(OTHER_USER)

    assert len(allowed) == 2
    assert denied == []
    assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls
    assert (OTHER_USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls
