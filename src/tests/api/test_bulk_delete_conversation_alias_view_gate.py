"""SECURITY: bulk-delete must not include messages in a channel the caller
cannot view, even when addressed via the conversation-id alias.

Message routes accept the conversation id that backs a server channel as an
alias for that channel. A live view gate keyed only on the URL id misses that
alias (``channel_exists``/``get_channel`` report "not a channel" for it), so a
member denied ``channels.view`` on a private channel could still bulk-delete
messages there -- including other users' messages when they hold the OWNER
role seeded on channel creation. The service must re-check the live view grant
on the channel backing each target message's conversation.

Run with: pytest src/tests/api/test_bulk_delete_conversation_alias_view_gate.py -q -n 0
"""

import pytest

import src.api.routes.messages.messages_crud.retrieve as retrieve_mod
from src.core.messaging.models import ParticipantRole
from src.core.messaging.services.message import edit_delete as edit_delete_mod
from src.core.messaging.services.message.edit_delete import EditDeleteMixin

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CHANNEL_ID = 8101  # real server channel id
CONV_ID = 8102  # conversation id backing that channel (the URL alias)
MESSAGE_ID = 8103
OTHER_MESSAGE_ID = 8104
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
            OTHER_MESSAGE_ID: _Row(
                id=OTHER_MESSAGE_ID,
                conversation_id=CONV_ID,
                author_id=VIEWER_ID,
                deleted=False,
            ),
        }
        self.soft_deleted = []

    def get_by_id(self, message_id):
        return self.rows.get(message_id)

    def soft_delete(self, message_id, now):
        self.soft_deleted.append(message_id)

    def hard_delete(self, message_id):
        raise AssertionError("hard_delete must not be used here")


class _Participant:
    def __init__(self, role):
        self.role = role


class _Participants:
    """Caller is a participant/OWNER in the conversation, regardless of view."""

    def is_participant(self, conversation_id, user_id):
        return True

    def get_participant(self, conversation_id, user_id):
        return _Participant(ParticipantRole.OWNER)


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


def _service(monkeypatch, servers):
    """Build the real bulk-delete service with isolated, inert side effects."""
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
    # The URL-id gate is what the alias bypasses; keep it out of the way so the
    # test isolates the per-message backing-channel check under test.
    monkeypatch.setattr(retrieve_mod, "_require_channel_view", lambda *a, **k: None)
    return svc, repo


def test_bulk_delete_skips_message_when_backing_channel_view_denied(monkeypatch):
    """Core leak: OWNER participant denied view cannot bulk-delete via alias."""
    servers = _Servers(view_allowed=False)
    svc, repo = _service(monkeypatch, servers)

    result = svc.delete_messages_bulk(
        VIEWER_ID, [MESSAGE_ID, OTHER_MESSAGE_ID], conversation_id=CONV_ID
    )

    assert result["deleted_ids"] == []
    assert result["failed_ids"] == [MESSAGE_ID, OTHER_MESSAGE_ID]
    assert repo.soft_deleted == []
    assert (CHANNEL_ID, VIEWER_ID) in servers.get_channel_calls


def test_bulk_delete_allows_message_when_backing_channel_view_granted(monkeypatch):
    """Control: with channels.view on the backing channel, deletes proceed."""
    servers = _Servers(view_allowed=True)
    svc, repo = _service(monkeypatch, servers)

    result = svc.delete_messages_bulk(
        VIEWER_ID, [MESSAGE_ID, OTHER_MESSAGE_ID], conversation_id=CONV_ID
    )

    assert result["deleted_ids"] == [MESSAGE_ID, OTHER_MESSAGE_ID]
    assert sorted(repo.soft_deleted) == sorted([MESSAGE_ID, OTHER_MESSAGE_ID])
    assert servers.get_channel_calls == [
        (CHANNEL_ID, VIEWER_ID)
    ]  # single cached decision


def test_bulk_delete_dm_message_is_not_gated_by_channel_view(monkeypatch):
    """Control: a DM/group conversation has no backing channel to gate."""
    servers = _Servers(view_allowed=False)
    svc, repo = _service(monkeypatch, servers)
    monkeypatch.setattr(servers, "get_channel_by_conversation", lambda cid: None)

    result = svc.delete_messages_bulk(VIEWER_ID, [MESSAGE_ID], conversation_id=CONV_ID)

    assert result["deleted_ids"] == [MESSAGE_ID]
    assert repo.soft_deleted == [MESSAGE_ID]
    assert servers.get_channel_calls == []
