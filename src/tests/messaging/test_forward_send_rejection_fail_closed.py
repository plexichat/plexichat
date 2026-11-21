"""Forwarding must fail closed when the gated live send rejects the message.

``ForwardingService._safe_send_via_messaging`` used to swallow *every*
exception from ``messaging.send_message`` and let ``forward_message`` still
insert a ``msg_forwarded`` row. A rejection by the send gate
(``InvalidContentError`` from the content filter,
``ConversationAccessDeniedError`` from the participation/permission check,
slowmode, blocks, ...) was laundered into a recorded forward carrying a
fabricated ``new_message_id`` for a message that never existed. The endpoint
reported the forward as delivered even though the target conversation never
accepted the content.

The send now propagates and no forward is recorded. These tests pin both the
rejection path and the normal success path (so they cannot pass vacuously).

Run with: pytest src/tests/messaging/test_forward_send_rejection_fail_closed.py -q
"""

from types import SimpleNamespace

import pytest

from src.core.messaging.exceptions import (
    ConversationAccessDeniedError,
    InvalidContentError,
)
from src.core.messaging.services.forwarding import ForwardingService

# id, conversation_id, author_id, content, content_encrypted, created_at
_MESSAGE_ROW = (20, 2, 5, "forwarded payload", None, 100)


class _DB:
    """Records writes; returns a readable message and no prior forwards."""

    def __init__(self):
        self.executed = []

    def fetch_one(self, query, params=()):
        if "FROM msg_messages" in query:
            return _MESSAGE_ROW
        if "COUNT(*)" in query:
            return (0,)
        return None

    def fetch_all(self, query, params=()):
        return []

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    def is_participant(self, conversation_id, user_id):
        return True


class _Messaging:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(id=777)


def _service(error=None):
    db = _DB()
    messaging = _Messaging(error)
    return db, ForwardingService(db, messaging, _Participants(), None), messaging


def _forward_inserts(db):
    return sum(1 for query, _ in db.executed if "INSERT INTO msg_forwarded" in query)


@pytest.mark.messaging
@pytest.mark.parametrize(
    "error",
    [
        InvalidContentError("content rejected by the send filter"),
        ConversationAccessDeniedError("not a participant of target"),
    ],
)
def test_rejected_send_records_no_forward(error):
    db, service, messaging = _service(error)

    with pytest.raises(type(error)):
        service.forward_message(5, 20, 99)

    assert messaging.calls, "the gated send path was never invoked"
    assert _forward_inserts(db) == 0, (
        "a forward was recorded even though send_message rejected the message"
    )


@pytest.mark.messaging
def test_accepted_send_still_records_forward():
    """Control: a successful send records exactly one forward."""
    db, service, _messaging = _service()

    result = service.forward_message(5, 20, 99)

    assert _forward_inserts(db) == 1
    assert result["new_message_id"] == 777
