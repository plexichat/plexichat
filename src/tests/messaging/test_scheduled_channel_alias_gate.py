"""Scheduled-message channel gate must resolve a channel-id alias.

The create and dispatch paths both call ``_assert_channel_post_access`` to
enforce the live ``channels.view`` / timeout / ``messages.send`` gate that the
normal send route applies.  That helper used to resolve the backing server
channel with a raw::

    SELECT id, server_id FROM srv_channels WHERE conversation_id = ? ...

lookup and treat a miss as "genuine DM -> allow".  But these APIs also accept a
server channel's *own id* in the conversation slot (``is_participant`` falls
back to ``srv_channels WHERE conversation_id = ? OR id = ?``; the poll/bookmark/
edit/attachment gates probe for that alias too).  For that form the lookup
keyed only on ``conversation_id`` misses, so both the create gate and the
dispatch-time re-check were silently skipped: a member denied view/send on a
private channel could queue content that dispatch later posted through the
membership-only ``send_message`` path.

These tests pin the reverse lookup to resolve the authoritative channel row by
EITHER id form, and pin fail-closed behaviour at BOTH create and dispatch time,
while keeping genuine DMs and legitimate channel posts working.

Run with: pytest src/tests/messaging/test_scheduled_channel_alias_gate.py -q -n 0
"""

import time
from types import SimpleNamespace

import pytest

import src.api as api
from src.core.messaging.services.scheduled import ScheduledMessageService

USER_ID = 7
CHANNEL_ID = 4242  # a server channel id used as the conversation id (alias)
REAL_CONVERSATION_ID = 5555  # the channel's distinct linked conversation id
SERVER_ID = 200
SCHEDULED_ID = 999


def _future_ms() -> int:
    return int(time.time() * 1000) + 120_000


def _due_row():
    return {
        "id": SCHEDULED_ID,
        "conversation_id": CHANNEL_ID,
        "author_id": USER_ID,
        "content": "queued into a private channel via its id",
        "scheduled_at": int(time.time() * 1000) - 1000,
        "status": "pending",
        "message_id": None,
        "created_at": 1,
        "updated_at": 1,
        "attachments": None,
    }


class _DB:
    """DB stub whose ``srv_channels`` row is keyed by channel id, not conv id.

    A channel-id alias is stored in ``srv_channels.id``; its
    ``srv_channels.conversation_id`` is a different snowflake.  The old lookup
    (``WHERE conversation_id = ?``) missed, while the corrected lookup
    (``WHERE (conversation_id = ? OR id = ?)``) finds it.
    """

    def __init__(self, *, linked_channel=True, due=False):
        self.linked_channel = linked_channel
        self.due = due
        self.executed = []

    def fetch_one(self, query, params=()):
        if "COUNT(*)" in query:
            return (0,)
        if "FROM srv_channels" in query and self.linked_channel:
            # The stored row has id = CHANNEL_ID and conversation_id =
            # REAL_CONVERSATION_ID. The channel-id alias is addressed here, so
            # only a reverse lookup that also probes `id = ?` can find it; a
            # ``WHERE conversation_id = ?`` lookup alone misses it.
            if "OR id = ?" in query and CHANNEL_ID in params:
                return (CHANNEL_ID, SERVER_ID)
            if REAL_CONVERSATION_ID in params:
                return (CHANNEL_ID, SERVER_ID)
        return None

    def fetch_all(self, query, params=()):
        if self.due and "FROM msg_scheduled" in query:
            return [_due_row()]
        return []

    def execute(self, query, params=(), auto_commit=True):
        self.executed.append((query, params))

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    """Membership always passes, so any denial comes from the channel gate."""

    def __init__(self):
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        return True


def _servers(*, send=False, view=True, timed_out=False):
    """Wired servers module with controllable channel gate answers."""
    return SimpleNamespace(
        get_channel=lambda channel_id, user_id: object() if view else None,
        is_timed_out=lambda user_id, server_id: timed_out,
        get_permissions=lambda user_id, server_id, channel_id: {"messages.send": send},
    )


class _Messaging:
    def __init__(self):
        self.sent = []

    def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=12345)


def _patch(monkeypatch, servers):
    monkeypatch.setattr(api, "get_servers", lambda: servers)


@pytest.mark.messaging
class TestScheduledChannelAliasGate:
    def test_create_into_private_channel_via_alias_is_denied(self, monkeypatch):
        """A read-only member must not schedule into a channel by its id."""
        _patch(monkeypatch, _servers(send=False))
        db = _DB()
        service = ScheduledMessageService(db, _Participants())

        with pytest.raises(PermissionError):
            service.create_scheduled_message(USER_ID, CHANNEL_ID, "later", _future_ms())

        assert not [q for q, _ in db.executed if "INSERT INTO msg_scheduled" in q], (
            "scheduled message was persisted for a private channel that the "
            "author has no messages.send grant on"
        )

    def test_dispatch_into_private_channel_via_alias_is_blocked(self, monkeypatch):
        """The send-time re-check must also resolve the alias and fail closed."""
        _patch(monkeypatch, _servers(send=False))
        db = _DB(due=True)
        messaging = _Messaging()
        service = ScheduledMessageService(db)

        results = service.dispatch_due_messages(messaging)

        assert messaging.sent == [], (
            "due message was posted into a private channel after the alias "
            "gate was skipped"
        )
        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert any("status = 'failed'" in q for q, _ in db.executed)

    def test_genuine_dm_still_schedules(self, monkeypatch):
        """Control: no backing channel means a DM, gated by participation only."""
        _patch(monkeypatch, _servers())
        db = _DB(linked_channel=False)
        service = ScheduledMessageService(db, _Participants())

        service.create_scheduled_message(USER_ID, CHANNEL_ID, "later", _future_ms())

        inserts = [q for q, _ in db.executed if "INSERT INTO msg_scheduled" in q]
        assert len(inserts) == 1

    def test_alias_channel_with_send_grant_still_schedules(self, monkeypatch):
        """Control: a real channel alias with a send grant is not blanket-denied."""
        _patch(monkeypatch, _servers(send=True))
        db = _DB()
        service = ScheduledMessageService(db, _Participants())

        service.create_scheduled_message(USER_ID, CHANNEL_ID, "later", _future_ms())

        inserts = [q for q, _ in db.executed if "INSERT INTO msg_scheduled" in q]
        assert len(inserts) == 1

    def test_linked_conversation_id_is_also_gated(self, monkeypatch):
        """Control: the normal conversation-id form was always gated too."""
        _patch(monkeypatch, _servers(send=False))
        db = _DB()
        service = ScheduledMessageService(db, _Participants())

        with pytest.raises(PermissionError):
            service.create_scheduled_message(
                USER_ID, REAL_CONVERSATION_ID, "later", _future_ms()
            )

        assert not [q for q, _ in db.executed if "INSERT INTO msg_scheduled" in q]
