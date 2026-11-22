"""SECURITY: channel message search must enforce the authoritative server scope.

The search route resolves ``GET /channels/{id}/messages/search`` with
``get_channel(url_id)``/``channel_exists(url_id)``. Message routes also accept a
server channel's linked ``conversation_id`` as a URL alias; those two probes
only know channel ids and report "not a channel" for the alias. The route then
treats the alias as a DM conversation and SKIPS its per-channel
``messages.read`` gate -- a member denied read on a private channel can search
(and read) its messages simply by swapping the channel id for its linked
conversation id. ``is_participant`` does not help: server membership alone
grants participation in every channel of the server.

The authoritative layer (``RetrievalMixin.search_messages``) always receives
the conversation id stored on the server, so it must resolve the backing
channel/server from THAT id and enforce the read grant there, failing closed,
for the route path and for internal callers that never pass through the route.
A genuine DM/group has no backing channel and carries no server-scoped gate.

Run with: pytest src/tests/messaging/test_message_search_alias_authoritative_server_scope.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError
from src.core.messaging.services.message import retrieval as retrieval_mod
from src.core.messaging.services.message.retrieval import RetrievalMixin

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CHANNEL_ID = 9401  # real server channel id
CONV_ID = 9402  # conversation id backing that channel (the URL alias)
SERVER_ID = 9403
MESSAGE_ID = 9404
USER_ID = 51  # server member denied messages.read on the private channel


class _Repo:
    def __init__(self):
        self.search_calls = []

    def search(self, conversation_id, query, limit, before_id, after_id):
        self.search_calls.append((conversation_id, query, limit))
        return [{"id": MESSAGE_ID}]

    def row_to_model(self, row, pin):
        return _Message()


class _PinRepo:
    def get_batch_by_messages(self, ids):
        return {}


class _AttachmentRepo:
    def get_batch_by_messages(self, ids):
        return {}


class _Participants:
    def is_participant(self, conversation_id, user_id):
        # Membership in a server grants participation in ALL its channels,
        # regardless of the per-channel messages.read grant.
        return conversation_id == CONV_ID and user_id == USER_ID


class _Message:
    id = MESSAGE_ID
    author_id = 7
    attachments = []


def _service(monkeypatch, servers):
    svc = RetrievalMixin(db=None)
    repo = _Repo()
    svc._repo = repo
    svc._pin_repo = _PinRepo()
    svc._attachment_repo = _AttachmentRepo()
    svc._participant_svc = _Participants()
    monkeypatch.setattr(
        retrieval_mod, "_get_servers_module", lambda: servers, raising=False
    )
    monkeypatch.setattr(retrieval_mod, "logger", _Logger())
    return svc, repo


class _Logger:
    def debug(self, *a, **k):
        pass


class _Servers:
    """Wired store: conversation alias -> a real channel on a server."""

    def __init__(self, *, read_allowed=False, break_permission=False, backing=True):
        self.read_allowed = read_allowed
        self.break_permission = break_permission
        self.backing = backing
        self.permission_calls = []

    def get_channel_by_conversation(self, conversation_id):
        if self.backing and conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": SERVER_ID}
        return None

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        if self.break_permission:
            raise RuntimeError("permission store unavailable")
        return self.read_allowed


def test_read_denied_member_cannot_search_via_alias(monkeypatch):
    """Core leak: a member denied messages.read must not search via the alias."""
    servers = _Servers(read_allowed=False)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(ConversationAccessDeniedError):
        svc.search_messages(USER_ID, CONV_ID, "secret")

    assert repo.search_calls == []
    # The read grant is checked against the authoritative SERVER channel id,
    # not the conversation id used as the URL alias.
    assert (USER_ID, SERVER_ID, "messages.read", CHANNEL_ID) in servers.permission_calls


def test_read_granted_member_can_still_search_via_alias(monkeypatch):
    """Control: the gate must not block a reader (non-vacuous)."""
    servers = _Servers(read_allowed=True)
    svc, repo = _service(monkeypatch, servers)

    messages = svc.search_messages(USER_ID, CONV_ID, "harmless")

    assert len(messages) == 1
    assert repo.search_calls == [(CONV_ID, "harmless", 50)]
    assert (USER_ID, SERVER_ID, "messages.read", CHANNEL_ID) in servers.permission_calls


def test_search_gate_fails_closed(monkeypatch):
    """An unproven read-grant lookup must deny, never grant, the search."""
    servers = _Servers(read_allowed=True, break_permission=True)
    svc, repo = _service(monkeypatch, servers)

    with pytest.raises(ConversationAccessDeniedError):
        svc.search_messages(USER_ID, CONV_ID, "secret")

    assert repo.search_calls == []


def test_dm_conversation_not_gated_by_server_permission(monkeypatch):
    """Control: a DM/group has no backing server channel to gate."""
    servers = _Servers(read_allowed=False, backing=False)
    svc, repo = _service(monkeypatch, servers)

    messages = svc.search_messages(USER_ID, CONV_ID, "dm text")

    assert len(messages) == 1
    assert servers.permission_calls == []
