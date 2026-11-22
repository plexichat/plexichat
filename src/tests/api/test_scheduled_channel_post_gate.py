"""Scheduled messages must pass the live send path's channel gate.

A server member is not automatically allowed to post in every channel of that
server. ``ParticipantService.is_participant`` only proves DM participation or
server membership, so scheduling on that check alone let a member queue a
message into a restricted channel they cannot view/post in; dispatch then sent
it through the messaging layer, whose only gate is the same membership check.
These tests pin the create path to the channel/permission/timeout gate the
normal message-send route uses, and pin fail-closed behaviour when that data
is unavailable.

Run with: pytest src/tests/api/test_scheduled_channel_post_gate.py -q -n 0
"""

import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
import src.api.routes.features.scheduled_messages as scheduled_route

USER_ID = 7
CONVERSATION_ID = 4242
CHANNEL_ID = 100
SERVER_ID = 200


def _future_ms() -> int:
    return int(time.time() * 1000) + 120_000


class _DB:
    """DB stub: records scheduled inserts and reports a server-channel link."""

    def __init__(self, linked_channel=True):
        self.inserts = []
        self.linked_channel = linked_channel

    def fetch_one(self, query, params=()):
        if "COUNT(*)" in query:
            return (0,)
        if "FROM srv_channels" in query:
            return (CHANNEL_ID, SERVER_ID) if self.linked_channel else None
        return None

    def fetch_all(self, query, params=()):
        return []

    def execute(self, query, params=(), auto_commit=True):
        if "INSERT INTO msg_scheduled" in query:
            self.inserts.append(params)

        class _Cursor:
            rowcount = 1

            def close(self):
                pass

        return _Cursor()


class _Participants:
    """Membership always passes, proving any denial comes from the channel gate."""

    def __init__(self):
        self.checked = []

    def is_participant(self, conversation_id, user_id):
        self.checked.append((conversation_id, user_id))
        return True


def _servers(channel=None, timed_out=False, permissions=None):
    """Build a servers-module stub with controllable channel access answers."""
    return SimpleNamespace(
        get_channel=lambda channel_id, user_id: channel,
        is_timed_out=lambda user_id, server_id: timed_out,
        get_permissions=lambda user_id, server_id, channel_id: permissions,
    )


def _setup(monkeypatch, servers, linked_channel=True):
    db = _DB(linked_channel=linked_channel)
    participants = _Participants()
    messaging = SimpleNamespace(_manager=SimpleNamespace(_participant_svc=participants))
    monkeypatch.setattr(scheduled_route.api, "get_db", lambda: db)
    monkeypatch.setattr(scheduled_route.api, "get_messaging", lambda: messaging)
    monkeypatch.setattr(api, "get_servers", lambda: servers)
    body = scheduled_route.ScheduledMessageRequest(
        conversation_id=str(CONVERSATION_ID),
        content="scheduled body",
        scheduled_at=_future_ms(),
    )
    return db, participants, body


async def _create(body):
    return await scheduled_route.create_scheduled_message(
        body, current_user=SimpleNamespace(user_id=USER_ID)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "servers,detail",
    [
        # Member of the server but the channel is not viewable to them.
        (_servers(channel=None, permissions={"messages.send": True}), "channel"),
        # Timeout must block scheduling just as it blocks a live send.
        (
            _servers(
                channel=object(), timed_out=True, permissions={"messages.send": True}
            ),
            "timeout",
        ),
        # Channel visible but posting explicitly denied by a permission override.
        (
            _servers(channel=object(), permissions={"messages.send": False}),
            "permission",
        ),
        # No servers module at all: cannot verify -> deny.
        (None, "unavailable"),
    ],
)
async def test_channel_gate_denies_and_writes_nothing(monkeypatch, servers, detail):
    db, participants, body = _setup(monkeypatch, servers)

    with pytest.raises(HTTPException) as exc:
        await _create(body)

    assert exc.value.status_code == 403
    # The participation check still ran and passed; the denial is specifically
    # the new channel gate, not a weakened membership check.
    assert participants.checked == [(CONVERSATION_ID, USER_ID)]
    assert db.inserts == []


@pytest.mark.asyncio
async def test_allowed_channel_and_dm_still_schedule(monkeypatch):
    """The gate must not be a blanket denial: legitimate sends still work."""
    db, participants, body = _setup(
        monkeypatch,
        _servers(channel=object(), permissions={"messages.send": True}),
    )

    result = await _create(body)

    assert result["success"] is True
    assert participants.checked == [(CONVERSATION_ID, USER_ID)]
    assert len(db.inserts) == 1
    assert db.inserts[0][1] == CONVERSATION_ID
    assert db.inserts[0][2] == USER_ID

    # A DM has no backing server channel, so participation alone is sufficient.
    dm_db, dm_participants, dm_body = _setup(
        monkeypatch,
        _servers(),
        linked_channel=False,
    )
    dm_result = await _create(dm_body)

    assert dm_result["success"] is True
    assert dm_participants.checked == [(CONVERSATION_ID, USER_ID)]
    assert len(dm_db.inserts) == 1
