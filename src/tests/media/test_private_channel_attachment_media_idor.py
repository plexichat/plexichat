"""SECURITY: media access must respect per-channel view, not just participation.

``MediaManager.check_file_access`` step 2 grants a file to anyone who
participates in a conversation where the uploader attached it. For server
channels, ``is_participant`` is granted to *every* server member regardless of
the per-channel ``channels.view`` permission (see
``ParticipantService.is_participant``). That makes the media route an IDOR: a
member denied view on a private channel can still download its attachments
directly via ``/api/v1/media/attachments/{filename}``.

Peers already added live ``channels.view`` gates to message read/delete; this
test pins the equivalent boundary for the *file* access grant, which is a
separate code path (core media, not the message routes).

Run with: pytest src/tests/media/test_private_channel_attachment_media_idor.py -q -n 0
"""

import pytest

from src.core.media.manager import MediaManager

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHANNEL_ID = 7001
CONV_ID = 7002
UPLOADER_ID = 99
VIEWER_ID = 42
FILENAME = "private_channel_secret.png"


class _FakeDB:
    """Minimal DB stand-in returning the rows ``check_file_access`` selects."""

    def fetch_one(self, sql, params=()):
        if "FROM media_files" in sql:
            return {"id": 1, "uploaded_by": UPLOADER_ID, "checksum": None}
        if "FROM auth_users" in sql or "FROM srv_servers" in sql:
            return {"count": 0}
        return None

    def fetch_all(self, sql, params=()):
        if "FROM msg_messages" in sql:
            # The uploader attached the file to a message in CONV_ID.
            return [{"conversation_id": CONV_ID}]
        # No avatar/icon/stash reference makes it public.
        return []


class _Messaging:
    """Stand-in whose participant gate is satisfied (server membership)."""

    def __init__(self):
        self.is_participant_calls = []

    def is_participant(self, conversation_id, user_id):
        self.is_participant_calls.append((conversation_id, user_id))
        return True


class _Servers:
    """Fake servers module with an authoritative per-channel view decision."""

    def __init__(self, *, view_allowed):
        self.view_allowed = view_allowed
        self.get_channel_calls = []

    def channel_exists(self, channel_id):
        # It IS a server channel, just not necessarily viewable.
        return True

    def get_channel_by_conversation(self, conversation_id):
        # Membership-agnostic reverse alias -> real backing channel.
        return {"id": CHANNEL_ID}

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls.append((channel_id, user_id))
        return object() if self.view_allowed else None


class _ServersNoReverse:
    """Channel id == conversation id; no reverse-alias helper available."""

    def __init__(self, *, view_allowed):
        self.view_allowed = view_allowed
        self.get_channel_calls = []

    def channel_exists(self, channel_id):
        return True

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls.append((channel_id, user_id))
        return object() if self.view_allowed else None


def _manager():
    manager = MediaManager.__new__(MediaManager)
    manager._db = _FakeDB()
    manager._messaging = _Messaging()
    manager._config = {}
    return manager


def _wire(monkeypatch, servers):
    import src.api as api

    monkeypatch.setattr(api, "get_servers", lambda: servers)
    return _manager()


def test_private_channel_attachment_denied_without_channel_view(monkeypatch):
    """Core leak: a server member denied channels.view must not read the file."""
    servers = _Servers(view_allowed=False)
    manager = _wire(monkeypatch, servers)

    assert manager.check_file_access(FILENAME, VIEWER_ID) is False
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]


def test_private_channel_attachment_allowed_with_channel_view(monkeypatch):
    """Legitimate access preserved: a member WITH channels.view may read it."""
    servers = _Servers(view_allowed=True)
    manager = _wire(monkeypatch, servers)

    assert manager.check_file_access(FILENAME, VIEWER_ID) is True
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]


def test_channel_id_alias_also_requires_view(monkeypatch):
    """The conversation-id == channel-id shape cannot bypass the view gate."""
    servers = _ServersNoReverse(view_allowed=False)
    manager = _wire(monkeypatch, servers)

    assert manager.check_file_access(FILENAME, VIEWER_ID) is False
    assert servers.get_channel_calls == [(CONV_ID, VIEWER_ID)]


def test_unwired_servers_module_fails_closed(monkeypatch):
    """When channel view cannot be evaluated, the grant must deny."""
    manager = _wire(monkeypatch, None)

    assert manager.check_file_access(FILENAME, VIEWER_ID) is False
