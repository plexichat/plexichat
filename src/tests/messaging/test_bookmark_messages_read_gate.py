"""SECURITY: bookmark listing must require the channel's ``messages.read``.

``BookmarkService.get_bookmarks`` gated a conversation-scoped listing (and each
enriched message) on ``is_participant`` alone. For a server channel that check
only proves membership of the parent server, so a member denied read by a
role/channel override could still read bookmarked message content via
``GET /bookmarks?conversation_id=...`` (and via the unfiltered listing).
The normal channel message listing path requires the per-channel
``messages.read`` grant; bookmark listing must do the same.

Run with: pytest src/tests/messaging/test_bookmark_messages_read_gate.py -q -n 0
"""

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


class _ParticipantSvc:
    """Models ParticipantService: server membership alone grants participation."""

    def is_participant(self, conversation_id, user_id):
        return True


class _Messaging:
    def __init__(self):
        self._participant_svc = _ParticipantSvc()
        self.messages = {
            MSG: SimpleNamespace(
                id=MSG,
                content="private-channel-secret",
                author_id=99,
                created_at=1700000000001,
                conversation_id=CONVERSATION,
            )
        }

    def get_messages_by_ids(self, user_id, ids):
        return {int(m): self.messages[int(m)] for m in ids if int(m) in self.messages}

    def get_message(self, user_id, mid):
        return self.messages.get(int(mid))


class _Servers:
    """channels.view granted; messages.read modelled independently."""

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
    """Inject a minimal ``src.api`` so the test is independent of route imports.

    The service resolves the servers module lazily via ``import src.api as
    api``. Supply a stub directly in ``sys.modules`` so this unit test never
    depends on the full API import graph being healthy.
    """
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
    return BookmarkService(_DB(conversation_id), _Messaging())


class TestBookmarkMessagesReadGate:
    def test_filtered_channel_denied_without_read(self, monkeypatch, _fake_api):
        servers = _Servers(messages_read=False)
        _wire(_fake_api, servers)

        # Before the fix this returned the bookmarked content because the only
        # gate was is_participant (True for every server member).
        with pytest.raises(PermissionError):
            _service(CHANNEL).get_bookmarks(USER, conversation_id=CHANNEL)
        assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls

    def test_filtered_channel_allowed_with_read(self, monkeypatch, _fake_api):
        servers = _Servers(messages_read=True)
        _wire(_fake_api, servers)

        rows = _service(CHANNEL).get_bookmarks(USER, conversation_id=CHANNEL)
        assert [r["message_id"] for r in rows] == [MSG]
        assert rows[0]["content_preview"] == "private-channel-secret"
        assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls

    def test_filtered_conversation_id_form_denied_without_read(
        self, monkeypatch, _fake_api
    ):
        """The conversation-id alias must resolve to the channel and gate it."""
        servers = _Servers(messages_read=False)
        _wire(_fake_api, servers)

        with pytest.raises(PermissionError):
            _service(CONVERSATION).get_bookmarks(USER, conversation_id=CONVERSATION)
        assert (USER, SERVER, "messages.read", CHANNEL) in servers.permission_calls

    def test_unfiltered_list_drops_denied_channel_content(self, monkeypatch, _fake_api):
        _wire(_fake_api, _Servers(messages_read=False))

        # The unfiltered path has no conversation filter to gate, so the
        # per-row check is what stops the content leak.
        assert _service(CHANNEL).get_bookmarks(USER) == []

    def test_unfiltered_list_keeps_allowed_channel_content(
        self, monkeypatch, _fake_api
    ):
        _wire(_fake_api, _Servers(messages_read=True))

        rows = _service(CHANNEL).get_bookmarks(USER)
        assert [r["message_id"] for r in rows] == [MSG]

    def test_permission_error_fails_closed(self, monkeypatch, _fake_api):
        _wire(_fake_api, _Servers(messages_read=True, read_raises=True))

        with pytest.raises(PermissionError):
            _service(CHANNEL).get_bookmarks(USER, conversation_id=CHANNEL)
        assert _service(CHANNEL).get_bookmarks(USER) == []

    def test_missing_permission_source_fails_closed(self, monkeypatch, _fake_api):
        _wire(_fake_api, _NoPermission(messages_read=True))

        with pytest.raises(PermissionError):
            _service(CHANNEL).get_bookmarks(USER, conversation_id=CHANNEL)

    def test_dm_needs_no_read_grant(self, monkeypatch, _fake_api):
        _wire(_fake_api, _Servers(messages_read=False, backing=False))

        rows = _service(CONVERSATION).get_bookmarks(USER, conversation_id=CONVERSATION)
        assert [r["message_id"] for r in rows] == [MSG]
