"""REGRESSION: unwired servers module must fail closed in the last-chat read gate.

``LastChatService._conversation_read_allowed`` decides whether the recent-chat
row for a conversation backed by a server channel may be returned. It resolves
the servers module to find the backing channel and require a live
``messages.read`` grant. The DB tables (channels / msg_conversations /
user_recent_chats) exist independently of that Python module, so a private
channel alias can be present while ``api.get_servers()`` reports the module as
unwired. In that state the reverse lookup cannot be evaluated; the gate used to
return ``True`` and leak the channel's unread count and last-message metadata.

Invariant (same as ``test_unwired_dependency_fails_closed``):

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

The wired controls keep the invariant non-vacuous: fail-closed must not become
a blanket denial of DMs or of conversations whose grant cannot be resolved for
other reasons (those already deny).

Run with::

    pytest -n 0 -q src/tests/messaging/test_unwired_servers_last_chat_read_gate.py
"""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from src.core.messaging.services.last_chat import LastChatService

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CHANNEL = 6000
CONVERSATION = 7000
SERVER = 7


class _DB:
    def __init__(self, conversation_id):
        self.rows = [
            {
                "conversation_id": conversation_id,
                "accessed_at": 1700000000000,
                "unread_count": 3,
                "last_message_at": 1700000000001,
                "last_message_id": 5000,
            }
        ]

    def fetch_all(self, query, params=None):
        if "FROM user_recent_chats" in query:
            return [dict(r) for r in self.rows]
        return []

    def fetch_one(self, query, params=None):
        return None


class _ParticipantSvc:
    def is_participant(self, conversation_id, user_id):
        return True


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
    return LastChatService(
        _DB(conversation_id), _ParticipantSvc()
    )._conversation_read_allowed(USER, conversation_id)


def test_unwired_servers_denies(_fake_api):
    _fake_api["servers"] = None
    assert _gate(_fake_api) is False


def test_unwired_servers_drops_private_channel_row(_fake_api):
    """Private-channel recent-chat rows exist in the DB even while unwired.

    The pre-fix gate returned the row; fail-closed must now drop it.
    """
    _fake_api["servers"] = None
    svc = LastChatService(_DB(CHANNEL), _ParticipantSvc())
    assert svc.get_recent_chats(USER) == []


def test_wired_genuine_dm_still_allowed(_fake_api):
    _fake_api["servers"] = _WiredStore(alias=None)
    assert _gate(_fake_api, CONVERSATION) is True


def test_wired_channel_alias_unresolvable_grant_denies(_fake_api):
    """A wired alias with no permission source cannot prove ``messages.read``."""
    _fake_api["servers"] = _WiredStore(
        alias={"id": CHANNEL, "server_id": SERVER}, has_permission=None
    )
    assert _gate(_fake_api) is False
