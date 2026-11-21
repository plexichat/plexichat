"""SECURITY: single-message edit must re-check the backing channel's view grant.

The edit route gates the URL channel id, but message routes also accept the
conversation id backing a server channel as an alias, and internal/service
callers (websocket handlers, background tasks, other services) call the message
service directly without ever passing through that route gate.

``ParticipantService.is_participant`` grants every member of a server
participation in its channel conversations independent of ``channels.view``.
So a member denied view on a private channel could still edit a message there
by calling ``edit_message`` with the backing conversation id. The service must
fail closed on the live ``get_channel`` grant for the channel backing the
message's conversation, exactly as bulk delete already does.

Run with: pytest src/tests/api/test_message_edit_single_conversation_alias_view_gate.py -q -n 0
"""

import pytest

from src.core.messaging.models import ParticipantRole
from src.core.messaging.exceptions import MessageNotFoundError
from src.core.messaging.services.message import edit_delete as edit_delete_mod
from src.core.messaging.services.message.edit_delete import EditDeleteMixin

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CHANNEL_ID = 8201  # real server channel id
CONV_ID = 8202  # conversation id backing that channel (the URL alias)
MESSAGE_ID = 8203
USER_ID = 42  # author, participant via server membership, denied channels.view


class _Row(dict):
    """get_by_id row with dict access used by the service."""


class _Repo:
    def __init__(self):
        self.row = _Row(
            id=MESSAGE_ID,
            conversation_id=CONV_ID,
            author_id=USER_ID,
            deleted=False,
            created_at=0,
            edited_at=None,
            updated_at=1,
            content="original content",
            metadata=None,
        )
        self.update_content_calls = []

    def get_by_id(self, message_id):
        return self.row if message_id == MESSAGE_ID else None

    def update_content(self, message_id, content, now, content_index=None):
        self.update_content_calls.append((message_id, content, now, content_index))


class _Participant:
    role = ParticipantRole.OWNER


class _Participants:
    """Caller is a participant/OWNER in the conversation regardless of view."""

    def is_participant(self, conversation_id, user_id):
        return True

    def get_participant(self, conversation_id, user_id):
        return _Participant()


class _Settings:
    max_message_length = 4000


class _UserSettings:
    def get_message_settings(self, user_id):
        return _Settings()


class _Validation:
    valid = True
    sanitized_content = None
    issues = []

    def __init__(self, content):
        self.sanitized_content = content


class _ContentFilter:
    def get_filter_settings(self, user_id):
        return object()

    def validate_content(self, content, user_filter, max_length):
        return _Validation(content)


class _Message:
    def __init__(self, message_id, conversation_id, content):
        self.id = message_id
        self.conversation_id = conversation_id
        self.content = content


class _Servers:
    def __init__(self, *, view_allowed: bool):
        self.view_allowed = view_allowed
        self.get_channel_calls = []

    def get_channel_by_conversation(self, conversation_id):
        if conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": 1}
        return None

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls.append((channel_id, user_id))
        if channel_id == CHANNEL_ID and self.view_allowed:
            return object()
        return None


def _service(monkeypatch, servers):
    """Build the real edit service with isolated, inert side effects."""
    svc = EditDeleteMixin(db=None)
    repo = _Repo()
    svc._repo = repo
    svc._participant_svc = _Participants()
    svc._user_settings_svc = _UserSettings()
    svc._content_filter_svc = _ContentFilter()
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda key, default=None: 0 if key == "edit_window_seconds" else default,
    )
    monkeypatch.setattr(svc, "_get_timestamp", lambda: 1700000000000)
    monkeypatch.setattr(
        svc, "get_message", lambda uid, mid: _Message(mid, CONV_ID, "new")
    )
    monkeypatch.setattr(edit_delete_mod, "_get_servers_module", lambda: servers)
    monkeypatch.setattr(edit_delete_mod, "blind_index", lambda *a, **k: "content-idx")
    monkeypatch.setattr(edit_delete_mod, "encrypt_message", lambda c, mid: c)
    monkeypatch.setattr(edit_delete_mod, "search_index_message", lambda *a, **k: None)
    monkeypatch.setattr(edit_delete_mod, "cache_delete", lambda *a, **k: None)
    monkeypatch.setattr(edit_delete_mod, "cache_set", lambda *a, **k: None)
    return svc, repo


def test_edit_single_denies_when_backing_channel_view_denied(monkeypatch):
    """Core leak: participant denied view cannot edit via the alias."""
    servers = _Servers(view_allowed=False)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(MessageNotFoundError):
        svc.edit_message(USER_ID, MESSAGE_ID, "pwned")

    assert repo.update_content_calls == []
    assert (CHANNEL_ID, USER_ID) in servers.get_channel_calls


def test_edit_single_allows_when_backing_channel_view_granted(monkeypatch):
    """Control: with channels.view on the backing channel, edit proceeds."""
    servers = _Servers(view_allowed=True)
    svc, repo = _service(monkeypatch, servers)

    svc.edit_message(USER_ID, MESSAGE_ID, "new content")

    assert len(repo.update_content_calls) == 1
    assert servers.get_channel_calls == [(CHANNEL_ID, USER_ID)]


def test_edit_single_dm_not_gated_by_channel_view(monkeypatch):
    """Control: a DM/group conversation has no backing channel to gate."""
    servers = _Servers(view_allowed=False)
    monkeypatch.setattr(servers, "get_channel_by_conversation", lambda cid: None)
    svc, repo = _service(monkeypatch, servers)

    svc.edit_message(USER_ID, MESSAGE_ID, "new content")

    assert len(repo.update_content_calls) == 1
    assert servers.get_channel_calls == []


def test_edit_single_fails_closed_on_reverse_lookup_error(monkeypatch):
    """An unproven alias lookup must deny, never grant, the edit."""
    servers = _Servers(view_allowed=True)

    def boom(conversation_id):
        raise RuntimeError("lookup unavailable")

    monkeypatch.setattr(servers, "get_channel_by_conversation", boom)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(MessageNotFoundError):
        svc.edit_message(USER_ID, MESSAGE_ID, "pwned")

    assert repo.update_content_calls == []
