"""REGRESSION: unwired servers module must fail closed in the bookmark read gate.

``BookmarkService._conversation_read_allowed`` decides whether bookmarked
messages of a conversation backed by a server channel may be read. It resolves
the servers module to find the backing channel and require a live
``messages.read`` grant. The DB tables (channels / msg_conversations / bookmarks)
exist independently of that Python module, so a private channel alias can be
present while ``api.get_servers()`` reports the module as unwired. In that state
the reverse lookup cannot be evaluated; the gate used to return ``True`` and
let a member denied read by a role/channel override see the bookmarked content.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep the invariant non-vacuous: fail-closed must not become
a blanket denial of DMs or of conversations whose grant cannot be resolved for
other reasons (those already deny).

Run with::

    pytest -n 0 -q src/tests/messaging/test_unwired_servers_bookmark_read_gate.py
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.core.messaging.services.bookmarks import BookmarkService

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
MSG = 5000
CHANNEL = 6000
CONVERSATION = 7000
SERVER = 7


class _DB:
    def __init__(self, conversation_id):
        self.rows = [
            {
                "id": 1,
                "user_id": USER,
                "message_id": MSG,
                "conversation_id": conversation_id,
                "label": None,
                "created_at": 1700000000000,
            }
        ]

    def fetch_all(self, query, params=None):
        if "FROM user_bookmarks" in query:
            return [dict(r) for r in self.rows]
        return []

    def fetch_one(self, query, params=None):
        return None


class _Messaging:
    def __init__(self):
        self._participant_svc = SimpleNamespace(is_participant=lambda c, u: True)
        self.messages = {
            MSG: SimpleNamespace(
                id=MSG,
                content="private-channel-secret",
                author_id=99,
                created_at=1700000000001,
                conversation_id=CHANNEL,
            )
        }

    def get_messages_by_ids(self, user_id, ids):
        return {int(m): self.messages[int(m)] for m in ids if int(m) in self.messages}

    def get_message(self, user_id, mid):
        return self.messages.get(int(mid))


class _WiredStore:
    """A wired servers store; ``alias=None`` models a genuine DM/group."""

    def __init__(self, alias, has_permission=None):
        self.alias = alias
        self.has_permission = has_permission

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL

    def get_channel_by_conversation(self, conversation_id):
        return self.alias


@pytest.fixture(autouse=True)
def _fake_api(monkeypatch):
    """Inject a minimal ``src.api`` so the unit gates resolve lazily."""
    fake = ModuleType("src.api")
    holder = {"servers": None}
    fake.get_servers = lambda: holder["servers"]
    monkeypatch.setitem(sys.modules, "src.api", fake)
    return holder


def _gate(holder, conversation_id=CHANNEL):
    return BookmarkService(
        _DB(conversation_id), _Messaging()
    )._conversation_read_allowed(USER, conversation_id)


def test_unwired_servers_denies(_fake_api):
    _fake_api["servers"] = None
    assert _gate(_fake_api) is False


def test_unwired_servers_drops_private_channel_row(_fake_api):
    """Private-channel bookmarks exist in the DB even while servers is unwired.

    The pre-fix gate allowed the row through; fail-closed must now raise.
    """
    _fake_api["servers"] = None
    svc = BookmarkService(_DB(CHANNEL), _Messaging())
    with pytest.raises(PermissionError):
        svc.get_bookmarks(USER, conversation_id=CHANNEL)


def test_wired_genuine_dm_still_allowed(_fake_api):
    _fake_api["servers"] = _WiredStore(alias=None)
    assert _gate(_fake_api, CONVERSATION) is True


def test_wired_channel_alias_unresolvable_grant_denies(_fake_api):
    """A wired alias with no permission source cannot prove ``messages.read``."""
    _fake_api["servers"] = _WiredStore(
        alias={"id": CHANNEL, "server_id": SERVER}, has_permission=None
    )
    assert _gate(_fake_api) is False
