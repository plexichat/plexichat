"""SECURITY (follow-up to N721 / e91a5bf3): alias message search must fail
CLOSED when the authoritative backing-channel lookup cannot be proven.

The N721 fix gates a channel's linked ``conversation_id`` URL alias on the real
backing channel's ``messages.read`` grant. It resolves that backing channel with
``get_channel_by_conversation(conversation_id)``, which returns ``None`` for a
genuine DM/group (no backing channel, no server-scoped gate) -- but the fix also
treats these *unproven* lookup outcomes as "no backing channel":

  * the reverse lookup raises, or
  * a wired, initialized module does not expose it at all, or
  * it returns a row with a missing / non-integer channel or server id.

All three collapse to "looks like a DM" and SKIP the ``messages.read`` gate, so
a member denied read on a private channel can still search it via its alias
whenever the lookup is broken. Every peer alias gate (message list, get-by-id,
read receipts, pins, reaction writes, bookmark listing) fails closed on exactly
these outcomes; this one does not.

These tests pin that an unproven lookup denies, while a clean ``None`` (a
genuine DM) and a valid backing channel still work -- i.e. the fix must not
over-deny DMs and must remain non-vacuous.

Run with: pytest src/tests/messaging/test_message_search_alias_unproven_lookup_fails_closed.py -q -n 0
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError
from src.core.messaging.services.message import retrieval as retrieval_mod
from src.core.messaging.services.message.retrieval import RetrievalMixin

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.messaging]

CHANNEL_ID = 9501
CONV_ID = 9502
SERVER_ID = 9503
MESSAGE_ID = 9504
USER_ID = 71


class _Message:
    id = MESSAGE_ID
    author_id = 7
    attachments = []


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
        # Server membership grants participation regardless of messages.read.
        return conversation_id == CONV_ID and user_id == USER_ID


class _Logger:
    def debug(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass


def _service(monkeypatch, servers):
    svc = RetrievalMixin(db=None)
    svc._repo = _Repo()
    svc._pin_repo = _PinRepo()
    svc._attachment_repo = _AttachmentRepo()
    svc._participant_svc = _Participants()
    monkeypatch.setattr(retrieval_mod, "_get_servers_module", lambda: servers)
    monkeypatch.setattr(retrieval_mod, "logger", _Logger(), raising=False)
    return svc


class _RaisingLookup:
    """Wired, initialized store whose reverse lookup blows up."""

    _setup_complete = True

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        raise AssertionError("must not reach the permission gate on a broken lookup")

    def get_channel_by_conversation(self, conversation_id):
        raise RuntimeError("channel store unavailable")


class _MissingLookup:
    """Wired, initialized store with no reverse-lookup API at all."""

    _setup_complete = True

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        raise AssertionError("must not reach the permission gate on a broken lookup")


class _CorruptRow:
    """Reverse lookup returns a backing row without a usable channel/server id."""

    _setup_complete = True

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        raise AssertionError("must not reach the permission gate on a broken lookup")

    def get_channel_by_conversation(self, conversation_id):
        return {"id": None, "server_id": SERVER_ID}


class _DM:
    """Clean 'no backing channel' answer: a genuine DM/group."""

    _setup_complete = True

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        raise AssertionError("a genuine DM must not consult the server permission gate")

    def get_channel_by_conversation(self, conversation_id):
        return None


class _GoodBacking:
    """Valid backing channel; read grant is controllable."""

    _setup_complete = True

    def __init__(self, read_allowed):
        self.read_allowed = read_allowed
        self.permission_calls = []

    def get_channel_by_conversation(self, conversation_id):
        if conversation_id == CONV_ID:
            return {"id": CHANNEL_ID, "server_id": SERVER_ID}
        return None

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.permission_calls.append((user_id, server_id, permission, channel_id))
        return self.read_allowed


def test_raising_reverse_lookup_fails_closed(monkeypatch):
    svc = _service(monkeypatch, _RaisingLookup())
    with pytest.raises(ConversationAccessDeniedError):
        svc.search_messages(USER_ID, CONV_ID, "secret")
    assert svc._repo.search_calls == []


def test_missing_reverse_api_fails_closed(monkeypatch):
    svc = _service(monkeypatch, _MissingLookup())
    with pytest.raises(ConversationAccessDeniedError):
        svc.search_messages(USER_ID, CONV_ID, "secret")
    assert svc._repo.search_calls == []


def test_corrupt_backing_row_fails_closed(monkeypatch):
    svc = _service(monkeypatch, _CorruptRow())
    with pytest.raises(ConversationAccessDeniedError):
        svc.search_messages(USER_ID, CONV_ID, "secret")
    assert svc._repo.search_calls == []


def test_genuine_dm_still_searchable(monkeypatch):
    """Control: a clean 'no backing channel' answer is not an unproven lookup."""
    svc = _service(monkeypatch, _DM())
    messages = svc.search_messages(USER_ID, CONV_ID, "dm text")
    assert len(messages) == 1
    assert svc._repo.search_calls == [(CONV_ID, "dm text", 50)]


def test_valid_backing_honors_read_grant(monkeypatch):
    """Control: the legitimate alias path is still gated and still works."""
    servers = _GoodBacking(read_allowed=True)
    svc = _service(monkeypatch, servers)
    assert len(svc.search_messages(USER_ID, CONV_ID, "hello")) == 1
    assert (USER_ID, SERVER_ID, "messages.read", CHANNEL_ID) in servers.permission_calls

    denied = _GoodBacking(read_allowed=False)
    svc2 = _service(monkeypatch, denied)
    with pytest.raises(ConversationAccessDeniedError):
        svc2.search_messages(USER_ID, CONV_ID, "secret")
    assert svc2._repo.search_calls == []
