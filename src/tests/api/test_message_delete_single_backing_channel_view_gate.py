"""SECURITY: single message delete must not act on a message in a channel the
caller cannot view, even when addressed via the conversation-id alias.

Message routes accept the conversation id that backs a server channel as an
alias for that channel. A live view gate keyed only on the URL id misses that
alias (``channel_exists``/``get_channel`` report "not a channel" for it), so a
member denied ``channels.view`` on a private channel could still delete
messages there -- including other users' messages when they hold the OWNER
role seeded on channel creation. The bulk path was already gated per target;
the single-delete service must re-check the live view grant on the channel
backing the target message's conversation too.

Run with: pytest src/tests/api/test_message_delete_single_backing_channel_view_gate.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import MessageNotFoundError
from src.core.messaging.models import ParticipantRole
from src.core.messaging.services.message import edit_delete as edit_delete_mod
from src.core.messaging.services.message.edit_delete import EditDeleteMixin

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CHANNEL_ID = 8201  # real server channel id
CONV_ID = 8202  # conversation id backing that channel (the URL alias)
MESSAGE_ID = 8203
VIEWER_ID = 42
AUTHOR_ID = 99


class _Row(dict):
    """get_by_id row with attribute-free dict access used by the service."""


class _Repo:
    def __init__(self):
        self.rows = {
            MESSAGE_ID: _Row(
                id=MESSAGE_ID,
                conversation_id=CONV_ID,
                author_id=AUTHOR_ID,
                deleted=False,
            ),
        }
        self.soft_deleted = []
        self.hard_deleted = []

    def get_by_id(self, message_id):
        return self.rows.get(message_id)

    def soft_delete(self, message_id, now):
        self.soft_deleted.append(message_id)

    def hard_delete(self, message_id):
        self.hard_deleted.append(message_id)


class _Participant:
    role = ParticipantRole.OWNER


class _Participants:
    """Caller is a participant/OWNER in the conversation, regardless of view."""

    def is_participant(self, conversation_id, user_id):
        return True

    def get_participant(self, conversation_id, user_id):
        return _Participant()


class _Servers:
    def __init__(self, *, view_allowed: bool):
        self.view_allowed = view_allowed
        self.get_channel_calls = []

    def get_channel_conversation_id(self, channel_id):
        return CONV_ID if channel_id == CHANNEL_ID else None

    def get_channel_by_conversation(self, conversation_id):
        if conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": 1}
        return None

    def get_channel(self, channel_id, user_id):
        self.get_channel_calls.append((channel_id, user_id))
        if channel_id == CHANNEL_ID and self.view_allowed:
            return object()
        return None

    def has_permission(self, *a, **k):
        return False


def _service(monkeypatch, servers):
    """Build the real single-delete service with isolated, inert side effects."""
    svc = EditDeleteMixin(db=None)
    repo = _Repo()
    svc._repo = repo
    svc._participant_svc = _Participants()
    monkeypatch.setattr(edit_delete_mod, "_get_servers_module", lambda: servers)
    monkeypatch.setattr(
        edit_delete_mod, "search_remove_from_index", lambda *a, **k: None
    )
    monkeypatch.setattr(edit_delete_mod, "cache_delete", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_cleanup_thread_links", lambda *a, **k: None)
    return svc, repo


def test_single_delete_denied_when_backing_channel_view_denied(monkeypatch):
    """Core leak: OWNER participant denied view cannot delete via the alias."""
    servers = _Servers(view_allowed=False)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(MessageNotFoundError):
        svc.delete_message(VIEWER_ID, MESSAGE_ID)

    assert repo.soft_deleted == []
    assert repo.hard_deleted == []
    assert (CHANNEL_ID, VIEWER_ID) in servers.get_channel_calls


def test_single_delete_allowed_when_backing_channel_view_granted(monkeypatch):
    """Control: with channels.view on the backing channel, delete proceeds."""
    servers = _Servers(view_allowed=True)
    svc, repo = _service(monkeypatch, servers)

    assert svc.delete_message(VIEWER_ID, MESSAGE_ID) is True
    assert repo.soft_deleted == [MESSAGE_ID]
    assert servers.get_channel_calls == [(CHANNEL_ID, VIEWER_ID)]


def test_single_delete_dm_message_is_not_gated_by_channel_view(monkeypatch):
    """Control: a DM/group conversation has no backing channel to gate."""
    servers = _Servers(view_allowed=False)
    svc, repo = _service(monkeypatch, servers)
    monkeypatch.setattr(servers, "get_channel_by_conversation", lambda cid: None)

    assert svc.delete_message(VIEWER_ID, MESSAGE_ID) is True
    assert repo.soft_deleted == [MESSAGE_ID]
    assert servers.get_channel_calls == []
