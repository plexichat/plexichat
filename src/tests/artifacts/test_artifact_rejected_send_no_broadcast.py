"""SECURITY: a rejected inline-message send must not be broadcast as a message.

``_emit_artifact_message`` persists the inline artifact message through the
gated ``messaging.send_message`` path (participation, DM blocks, content
filter, length/attachment limits) and then dispatches a MESSAGE_CREATE event to
the conversation's audience. That dispatch used to run even when
``send_message`` *rejected* the send and its exception was swallowed. A member
could put filtered/attacker-controlled content in an artifact title, have the
send path refuse it, and still get a phantom message carrying that content
pushed to every participant's client -- content that was never persisted and
never passed the filter.

The fix fails closed: a rejected send skips the event broadcast. These tests
pin the rejection path (both a content-filter and an access-denial rejection)
and a control proving legitimate sends still broadcast, so they cannot pass
vacuously.

Run with: pytest src/tests/artifacts/test_artifact_rejected_send_no_broadcast.py -q -n 0
"""

from types import SimpleNamespace

import pytest

import src.api as api
from src.api.routes import artifacts_helpers as helpers
from src.core.messaging.exceptions import (
    ConversationAccessDeniedError,
    InvalidContentError,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

AUTHOR = 7
CONV = 4242
CHANNEL = 100
SERVER = 200


class _Messaging:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id=999)

    def get_participant_ids(self, conversation_id):
        return [AUTHOR]


class _Servers:
    """The channel send gate passes so only the send path itself can reject."""

    def get_channel_by_conversation(self, conversation_id):
        return {"id": CHANNEL, "server_id": SERVER}

    def get_channel(self, channel_id, user_id):
        return object()

    def is_timed_out(self, user_id, server_id):
        return False

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        return True


def _artifact():
    return SimpleNamespace(
        id=555,
        conversation_id=CONV,
        channel_id=CHANNEL,
        server_id=SERVER,
        author_id=AUTHOR,
        title="attacker-controlled / filtered payload",
    )


@pytest.fixture
def env(monkeypatch):
    import src.core.events as events

    messaging = _Messaging()
    dispatched = []
    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    monkeypatch.setattr(api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(events, "is_setup", lambda: True)
    monkeypatch.setattr(
        events, "dispatch", lambda event, **kwargs: dispatched.append((event, kwargs))
    )
    return {"messaging": messaging, "dispatched": dispatched}


@pytest.mark.parametrize(
    "error",
    [
        InvalidContentError("content rejected by the send filter"),
        ConversationAccessDeniedError("not a participant in target conversation"),
    ],
)
def test_rejected_send_is_not_broadcast(env, monkeypatch, error):
    messaging = env["messaging"]
    messaging.error = error

    helpers._emit_artifact_message(_artifact(), {"username": "member"})

    assert len(messaging.calls) == 1, "the gated send path was never invoked"
    assert env["dispatched"] == [], (
        "a MESSAGE_CREATE was broadcast even though send_message rejected the "
        "inline artifact message"
    )


def test_accepted_send_still_broadcasts(env):
    """Control: a successful send still emits exactly one MESSAGE_CREATE."""
    helpers._emit_artifact_message(_artifact(), {"username": "member"})

    assert len(env["messaging"].calls) == 1
    assert len(env["dispatched"]) == 1
