"""SECURITY: artifact inline messages must pass the live channel send gate.

``_emit_artifact_message`` persists a MESSAGE_CREATE straight through
``messaging.send_message``, whose only gate is conversation participation. For
a server channel participation is seeded for every member on join and is NOT a
send grant. Artifact creation itself is gated by ``artifact.create``, which is
granted to @everyone by default and says nothing about posting in the specific
backing channel. Without a live messages.send / timeout / channel-view guard a
member who may only view a read-only channel -- or who is timed out in the
server -- can create an artifact and have a message carrying their chosen title
posted into a channel where the REST send route would reject them.

Run with: pytest src/tests/artifacts/test_artifact_inline_message_send_gate.py -q -n 0
"""

from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes import artifacts_helpers as helpers

pytestmark = [pytest.mark.unit, pytest.mark.security]

AUTHOR = 7
CONV = 4242
CHANNEL = 100
SERVER = 200


class _Servers:
    """Membership/view satisfied unless the test flips it; only the gate denies."""

    def __init__(
        self,
        *,
        linked=True,
        view=True,
        allow_send=True,
        timed_out=False,
        lookup_error=False,
        perm_error=False,
        no_lookup=False,
        no_timeout=False,
        no_view=False,
        no_permission=False,
        incomplete_info=False,
    ):
        self.linked = linked
        self.view = view
        self.allow_send = allow_send
        self.timed_out = timed_out
        self.lookup_error = lookup_error
        self.perm_error = perm_error
        self.calls = []
        if no_lookup:
            self.get_channel_by_conversation = None
        if no_timeout:
            self.is_timed_out = None
        if no_view:
            self.get_channel = None
        if no_permission:
            self.has_permission = None
        self.incomplete_info = incomplete_info

    def get_channel_by_conversation(self, conversation_id):
        self.calls.append(("lookup", int(conversation_id)))
        if self.lookup_error:
            raise RuntimeError("lookup down")
        if not self.linked:
            return None
        if self.incomplete_info:
            return {"id": CHANNEL}
        return {"id": CHANNEL, "server_id": SERVER}

    def get_channel(self, channel_id, user_id):
        self.calls.append(("get_channel", int(channel_id), user_id))
        return object() if self.view else None

    def is_timed_out(self, user_id, server_id):
        self.calls.append(("is_timed_out", user_id, server_id))
        return self.timed_out

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append(
            ("has_permission", user_id, server_id, permission, channel_id)
        )
        if self.perm_error:
            raise RuntimeError("permission service down")
        return self.allow_send


class _Messaging:
    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=999)

    def get_participant_ids(self, conversation_id):
        return [AUTHOR]


def _artifact(conversation_id=CONV):
    return SimpleNamespace(
        id=555,
        conversation_id=conversation_id,
        channel_id=CHANNEL,
        server_id=SERVER,
        author_id=AUTHOR,
        title="post into a read-only channel",
    )


@pytest.fixture
def env(monkeypatch):
    import src.core.events as events

    servers = _Servers()
    messaging = _Messaging()
    dispatched = []
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(events, "is_setup", lambda: True)
    monkeypatch.setattr(
        events, "dispatch", lambda event, **kwargs: dispatched.append((event, kwargs))
    )
    return {"servers": servers, "messaging": messaging, "dispatched": dispatched}


def _emit(artifact=None):
    helpers._emit_artifact_message(artifact or _artifact(), {"username": "member"})


def test_read_only_channel_message_is_not_persisted(env, monkeypatch):
    """A viewer without messages.send cannot post the inline artifact message."""
    servers = _Servers(view=True, allow_send=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    _emit()
    assert env["messaging"].sent == []
    assert env["dispatched"] == []
    # The denial is specifically the send grant, not a missing view/timeout.
    assert (
        "has_permission",
        AUTHOR,
        SERVER,
        "messages.send",
        CHANNEL,
    ) in servers.calls


def test_timed_out_author_message_is_not_persisted(env, monkeypatch):
    servers = _Servers(timed_out=True)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    _emit()
    assert env["messaging"].sent == []
    assert env["dispatched"] == []


def test_channel_not_viewable_message_is_not_persisted(env, monkeypatch):
    servers = _Servers(view=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    _emit()
    assert env["messaging"].sent == []
    assert env["dispatched"] == []


@pytest.mark.parametrize(
    "factory",
    [
        lambda: _Servers(lookup_error=True),
        lambda: _Servers(perm_error=True),
        lambda: _Servers(incomplete_info=True),
        lambda: _Servers(no_lookup=True),
        lambda: _Servers(no_view=True),
        lambda: _Servers(no_timeout=True),
        lambda: _Servers(no_permission=True),
    ],
)
def test_unresolvable_permissions_fail_closed(env, monkeypatch, factory):
    servers = factory()
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    _emit()
    assert env["messaging"].sent == []
    assert env["dispatched"] == []


def test_authorized_message_is_still_persisted(env):
    """Non-vacuous: the gate is not a blanket deny -- legitimate posts work."""
    _emit()
    assert len(env["messaging"].sent) == 1
    sent = env["messaging"].sent[0]
    assert sent["user_id"] == AUTHOR
    assert sent["conversation_id"] == CONV
    assert "post into a read-only channel" in sent["content"]
    assert len(env["dispatched"]) == 1


def test_dm_conversation_does_not_require_channel_send(env, monkeypatch):
    """A conversation with no backing server channel needs no send grant."""
    servers = _Servers(linked=False, allow_send=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    _emit()
    assert len(env["messaging"].sent) == 1
    assert len(env["dispatched"]) == 1


def test_message_would_land_without_the_gate(env, monkeypatch):
    """Prove the finding: with the gate bypassed the unauthorized post lands."""
    monkeypatch.setattr(helpers, "_can_emit_artifact_message", lambda *a: True)
    servers = _Servers(view=True, allow_send=False)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    _emit()
    assert len(env["messaging"].sent) == 1
