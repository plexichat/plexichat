"""SECURITY: single-message edit must enforce the authoritative server scope.

Message routes accept a server channel's linked ``conversation_id`` as a URL
alias. The edit route derives its moderation/timeout ``server_id`` from that
URL id via ``get_channel(url_id)``, which cannot see a conversation-id alias
as a channel -- so a timed-out member could keep editing their own messages
by addressing the channel through its conversation-id alias. The service
must derive the backing channel/server from the message's SERVER-SIDE
conversation id and enforce the timeout there, failing closed, for both the
route path and internal callers that bypass the route gate.

Run with: pytest src/tests/messaging/test_message_edit_alias_authoritative_server_scope.py -q -n 0
"""

import pytest

from src.core.messaging.models import ParticipantRole
from src.core.messaging.exceptions import MessageAccessDeniedError
from src.core.messaging.services.message import edit_delete as edit_delete_mod
from src.core.messaging.services.message.edit_delete import EditDeleteMixin

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CHANNEL_ID = 8301  # real server channel id
CONV_ID = 8302  # conversation id backing that channel (the URL alias)
SERVER_ID = 8303
MESSAGE_ID = 8304
USER_ID = 49  # author, participant, currently timed out on the server


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
            content_index=None,
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
    """Wired store: conversation aliases to a real channel on a server."""

    def __init__(self, *, timed_out=False, break_timeout=False, backing=True):
        self.timed_out = timed_out
        self.break_timeout = break_timeout
        self.backing = backing
        self.timeout_calls = []

    def get_channel_by_conversation(self, conversation_id):
        if self.backing and conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": SERVER_ID}
        return None

    def get_channel(self, channel_id, user_id):
        # A conversation-id alias is NOT a channel id; only the real id resolves.
        return object() if channel_id == CHANNEL_ID else None

    def is_timed_out(self, user_id, server_id):
        self.timeout_calls.append((user_id, server_id))
        if self.break_timeout:
            raise RuntimeError("timeout store unavailable")
        return self.timed_out


def _service(monkeypatch, servers):
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


def test_timed_out_author_cannot_edit_via_alias(monkeypatch):
    """Core leak: a timed-out author must not edit through the alias."""
    servers = _Servers(timed_out=True)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(MessageAccessDeniedError):
        svc.edit_message(USER_ID, MESSAGE_ID, "pwned")

    assert repo.update_content_calls == []
    # The timeout is checked against the authoritative SERVER id, not the
    # conversation id used as the URL alias.
    assert (USER_ID, SERVER_ID) in servers.timeout_calls


def test_non_timed_out_author_can_still_edit(monkeypatch):
    """Control: the gate must not block a normal author (non-vacuous)."""
    servers = _Servers(timed_out=False)
    svc, repo = _service(monkeypatch, servers)

    svc.edit_message(USER_ID, MESSAGE_ID, "legit edit")

    assert len(repo.update_content_calls) == 1
    assert (USER_ID, SERVER_ID) in servers.timeout_calls


def test_timed_out_check_fails_closed(monkeypatch):
    """An unproven timeout lookup must deny, never grant, the edit."""
    servers = _Servers(timed_out=False, break_timeout=True)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(MessageAccessDeniedError):
        svc.edit_message(USER_ID, MESSAGE_ID, "pwned")

    assert repo.update_content_calls == []


def test_dm_conversation_not_gated_by_server_timeout(monkeypatch):
    """Control: a DM/group has no backing server channel to gate."""
    servers = _Servers(timed_out=True, backing=False)
    svc, repo = _service(monkeypatch, servers)

    svc.edit_message(USER_ID, MESSAGE_ID, "legit dm edit")

    assert len(repo.update_content_calls) == 1
    assert servers.timeout_calls == []
