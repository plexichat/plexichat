"""Scheduled dispatch must re-check the author's LIVE channel send authority.

A member is gated on the channel's ``messages.send`` permission (plus view and
timeout) when a message is *scheduled*. The cron/worker that later *dispatches*
the due row only calls ``messaging.send_message``, whose sole authorization is
``is_participant`` (server membership). A member who loses ``messages.send``,
is timed out, or loses view of the channel between scheduling and dispatch
therefore still gets the queued message posted -- captured stale authority.

These tests drive ``ScheduledMessageService.dispatch_due_messages`` and pin
fail-closed behaviour: re-check the current channel gate at execution time,
mark the row failed, and never call ``send_message`` when authority is gone.

Run with: pytest src/tests/messaging/test_scheduled_dispatch_rechecks_channel_authority.py -q -n 0
"""

import time
from types import SimpleNamespace

import pytest

import src.api as api
from src.core.messaging.services.scheduled import ScheduledMessageService

USER_ID = 7
CONVERSATION_ID = 4242
CHANNEL_ID = 100
SERVER_ID = 200
SCHEDULED_ID = 999


def _due_row():
    """A single pending, due scheduled-message row (mapping form)."""
    return {
        "id": SCHEDULED_ID,
        "conversation_id": CONVERSATION_ID,
        "author_id": USER_ID,
        "content": "queued before authority was revoked",
        "scheduled_at": int(time.time() * 1000) - 1000,
        "status": "pending",
        "message_id": None,
        "created_at": 1,
        "updated_at": 1,
        "attachments": None,
    }


class _DB:
    """DB stub: one due row, one backing server channel, recorded writes."""

    def __init__(self):
        self.executed = []

    def fetch_all(self, query, params=()):
        if "FROM msg_scheduled" in query and "status = 'pending'" in query:
            return [_due_row()]
        return []

    def fetch_one(self, query, params=()):
        if "FROM srv_channels" in query:
            return (CHANNEL_ID, SERVER_ID)
        return None

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Messaging:
    """Records send attempts; a call here means the stale message was posted."""

    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=12345)


def _servers(*, send=True, view=True, timed_out=False):
    return SimpleNamespace(
        get_channel=lambda channel_id, user_id: object() if view else None,
        is_timed_out=lambda user_id, server_id: timed_out,
        get_permissions=lambda user_id, server_id, channel_id: {"messages.send": send},
    )


def _dispatch(monkeypatch, servers):
    db = _DB()
    if isinstance(servers, Exception):

        def _raise():
            raise servers

        monkeypatch.setattr(api, "get_servers", _raise)
    else:
        monkeypatch.setattr(api, "get_servers", lambda: servers)
    messaging = _Messaging()
    service = ScheduledMessageService(db)
    results = service.dispatch_due_messages(messaging)
    return db, messaging, results


@pytest.mark.messaging
class TestScheduledDispatchRechecksAuthority:
    def test_revoked_send_permission_blocks_dispatch(self, monkeypatch):
        """Losing messages.send after scheduling must stop the queued post."""
        db, messaging, results = _dispatch(monkeypatch, _servers(send=False))

        assert messaging.sent == [], (
            "due message was posted after the author lost messages.send"
        )
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert any("status = 'failed'" in q for q, _ in db.executed)

    def test_timeout_blocks_dispatch(self, monkeypatch):
        """A later timeout must also revoke the queued post."""
        _, messaging, results = _dispatch(
            monkeypatch, _servers(send=True, timed_out=True)
        )

        assert messaging.sent == []
        assert results[0]["status"] == "failed"

    def test_lost_channel_view_blocks_dispatch(self, monkeypatch):
        """Losing view of the channel must fail closed at dispatch."""
        _, messaging, results = _dispatch(monkeypatch, _servers(view=False))

        assert messaging.sent == []
        assert results[0]["status"] == "failed"

    def test_unavailable_checker_blocks_dispatch(self, monkeypatch):
        """Cannot verify authority -> deny, never post."""
        _, messaging, results = _dispatch(
            monkeypatch, RuntimeError("servers module unavailable")
        )

        assert messaging.sent == []
        assert results[0]["status"] == "failed"

    def test_intact_authority_still_dispatches(self, monkeypatch):
        """The re-check must not be a blanket denial: valid sends still go."""
        _, messaging, results = _dispatch(monkeypatch, _servers(send=True))

        assert len(messaging.sent) == 1
        assert messaging.sent[0]["conversation_id"] == CONVERSATION_ID
        assert messaging.sent[0]["user_id"] == USER_ID
        assert results[0]["status"] == "dispatched"
