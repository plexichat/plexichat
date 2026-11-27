"""SECURITY: recent-chat listing must require a channel's ``messages.read``.

``LastChatService.get_recent_chats`` gated every row on ``is_participant``
alone. For a server channel that check only proves membership of the parent
server, so a member denied read by a role/channel override still received the
conversation's unread count, last-message id and last-message timestamp via
``GET /users/@me/recent-chats``. Because ``save_last_chat`` has the same
membership-only gate, a member can even seed such a row for a private channel
and then poll it for activity. The normal channel message listing path
additionally requires the per-channel ``messages.read`` grant; recent-chat
listing must do the same.

Run with: pytest src/tests/messaging/test_recent_chats_messages_read_gate.py -q -n 0
"""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.core.messaging.services.last_chat import LastChatService

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER = 42
CHANNEL = 6000
CONVERSATION = 7000
SERVER = 7


class _DB:
    """One recent-chat row pointing at ``conversation_id``."""

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
    """Models ParticipantService: server membership alone grants participation."""

    def is_participant(self, conversation_id, user_id):
        return True


class _Servers:
    def __init__(self, *, messages_read=True, backing=True, read_raises=False):
        self.messages_read = messages_read
        self.backing = backing
        self.read_raises = read_raises
        self.permission_calls = []

    def channel_exists(self, channel_id):
        return channel_id == CHANNEL

    def get_channel_by_conversation(self, conversation_id):
        if not self.backing:
            return None
        return {"id": CHANNEL, "server_id": SERVER}

    def get_channel(self, channel_id, user_id):
        return SimpleNamespace(server_id=SERVER)  # channels.view granted

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        if self.read_raises:
            raise RuntimeError("permission store down")
        if permission == "messages.read":
            return self.messages_read
        return True


class _NoPermission(_Servers):
    """Resolves the channel but has no messages.read capability."""

    has_permission = None


@pytest.fixture(autouse=True)
def _fake_api(monkeypatch):
    """Inject a minimal ``src.api``; the service resolves servers lazily."""
    fake = ModuleType("src.api")
    holder = {"servers": None}
    fake.get_servers = lambda: holder["servers"]
    # ``import src.api as api`` binds the parent package attribute first, so
    # replacing ``sys.modules`` alone is not enough once another test module in
    # the same process has imported the real API: the stub must own the
    # ``src.api`` attribute or the service silently talks to the real module
    # and the stubbed servers store is ignored.
    monkeypatch.setitem(sys.modules, "src.api", fake)
    monkeypatch.setattr(sys.modules["src"], "api", fake, raising=False)
    return holder


def _wire(holder, servers):
    holder["servers"] = servers


def _service(conversation_id):
    return LastChatService(_DB(conversation_id), _ParticipantSvc())


class TestRecentChatsMessagesReadGate:
    def test_channel_row_dropped_without_read(self, _fake_api):
        servers = _Servers(messages_read=False)
        _wire(_fake_api, servers)

        # Before the fix this row was returned: the only gate was
        # is_participant (True for every server member).
        assert _service(CHANNEL).get_recent_chats(USER) == []
        assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls

    def test_channel_row_kept_with_read(self, _fake_api):
        servers = _Servers(messages_read=True)
        _wire(_fake_api, servers)

        rows = _service(CHANNEL).get_recent_chats(USER)
        assert len(rows) == 1
        assert rows[0]["last_message_id"] == 5000
        assert rows[0]["unread_count"] == 3
        assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls

    def test_conversation_id_alias_gated(self, _fake_api):
        """The conversation-id form must resolve to the channel and gate it."""
        servers = _Servers(messages_read=False)
        _wire(_fake_api, servers)

        assert _service(CONVERSATION).get_recent_chats(USER) == []
        assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls

    def test_dm_needs_no_read_grant(self, _fake_api):
        _wire(_fake_api, _Servers(messages_read=False, backing=False))

        rows = _service(CONVERSATION).get_recent_chats(USER)
        assert len(rows) == 1
        assert rows[0]["conversation_id"] == CONVERSATION

    def test_permission_error_fails_closed(self, _fake_api):
        _wire(_fake_api, _Servers(messages_read=True, read_raises=True))

        assert _service(CHANNEL).get_recent_chats(USER) == []

    def test_missing_permission_source_fails_closed(self, _fake_api):
        _wire(_fake_api, _NoPermission(messages_read=True))

        assert _service(CHANNEL).get_recent_chats(USER) == []
