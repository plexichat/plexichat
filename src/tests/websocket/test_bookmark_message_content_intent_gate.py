"""Bookmark frames must not embed message text without MESSAGE_CONTENT.

``MESSAGE_BOOKMARK_ADD`` / ``MESSAGE_BOOKMARK_REMOVE`` are authorized at the
intent gate by the *base* message intent (GUILD_MESSAGES / DIRECT_MESSAGES --
the same gate MESSAGE_ACK uses).  Their payload, however, also carries the
bookmarked message's body in ``message_content``, which is the separate
privileged MESSAGE_CONTENT payload.  Without a payload-level gate a connection
holding only the base intent receives the private text -- the same class of
leak as GUILD_MEMBERS_CHUNK carrying presence without GUILD_PRESENCES.

The gate lives at the last hop before the wire (``Connection.send_json``) so
it covers live fan-out, single-connection dispatch and RESUME replay alike.
These tests are non-vacuous: the positive control proves the frame is still
delivered intact when MESSAGE_CONTENT is declared, and the negative cases
prove only the embedded body is withheld, not the bookmark notification.
"""

import pytest

from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent

pytestmark = [pytest.mark.websocket, pytest.mark.security]

BOOKMARK_EVENTS = (EventType.MESSAGE_BOOKMARK_ADD, EventType.MESSAGE_BOOKMARK_REMOVE)
BODY = "private bookmarked message body"


class _Socket:
    """Minimal async transport that records the frames it was handed."""

    def __init__(self):
        self.frames = []
        self.client = None
        self.headers = {}

    async def send_json(self, payload):
        self.frames.append(payload)
        return True

    async def send_bytes(self, payload):
        raise AssertionError("compressed send is not expected in this test")

    async def close(self, code=1000, reason=""):
        return True


def _connection(intents):
    socket = _Socket()
    conn = Connection(
        websocket=socket,
        connection_id="bookmark-gate-conn",
        state=ConnectionState.READY,
        user_id=7,
        intents=intents,
    )
    return conn, socket


def _frame(event_type=EventType.MESSAGE_BOOKMARK_ADD):
    """Wire frame shape emitted by ``_dispatch_bookmark_event`` (scope-less)."""
    return {
        "op": 0,
        "t": event_type.value,
        "s": 7,
        "d": {
            "user_id": "7",
            "message_id": "99",
            "conversation_id": "42",
            "bookmark_id": "5",
            "label": None,
            "url": "https://example.com/m/99",
            "created_at": None,
            "message_content": BODY,
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", BOOKMARK_EVENTS)
async def test_bookmark_body_withheld_without_message_content(event_type):
    """GUILD_MESSAGES authorizes the notification, never the message body."""
    conn, socket = _connection(int(GatewayIntent.GUILD_MESSAGES))
    await conn.send_json(_frame(event_type))

    assert len(socket.frames) == 1  # non-vacuous: the frame was still sent
    frame = socket.frames[0]
    # The base payload survives untouched ...
    assert frame["d"]["bookmark_id"] == "5"
    assert frame["d"]["message_id"] == "99"
    assert frame["d"]["url"] == "https://example.com/m/99"
    # ... only the privileged body is scrubbed.
    assert frame["d"]["message_content"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", BOOKMARK_EVENTS)
async def test_bookmark_body_delivered_when_intent_declared(event_type):
    """Positive control: MESSAGE_CONTENT restores the body verbatim."""
    mask = int(GatewayIntent.GUILD_MESSAGES | GatewayIntent.MESSAGE_CONTENT)
    conn, socket = _connection(mask)
    await conn.send_json(_frame(event_type))

    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["message_content"] == BODY


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_mask", [0, None, -1, True, "garbage", 2**40])
async def test_bookmark_body_fails_closed_on_invalid_or_missing_mask(bad_mask):
    """No/absent/corrupt masks never grant the privileged body read."""
    conn, socket = _connection(bad_mask)
    await conn.send_json(_frame())

    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["message_content"] is None


@pytest.mark.asyncio
async def test_dm_scoped_bookmark_uses_direct_messages_for_content():
    """The helper's DM semantics are kept: DIRECT_MESSAGES authorizes DM body."""
    frame = _frame()
    frame["d"]["channel_id"] = "42"  # DM scope, no guild/server id
    conn, socket = _connection(int(GatewayIntent.DIRECT_MESSAGES))
    await conn.send_json(frame)

    assert socket.frames[0]["d"]["message_content"] == BODY


@pytest.mark.asyncio
async def test_end_to_end_dispatch_scrubs_bookmark_body():
    """The gateway dispatch path (not just a direct send) enforces the gate."""
    conn, socket = _connection(int(GatewayIntent.GUILD_MESSAGES))
    event = Event(
        event_type=EventType.MESSAGE_BOOKMARK_ADD,
        data=_frame()["d"],
    )
    dispatcher = GatewayDispatcher(session_manager=None)

    result = await dispatcher.dispatch_to_connection(conn, event)

    assert result is True  # base intent authorized delivery
    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["bookmark_id"] == "5"
    assert socket.frames[0]["d"]["message_content"] is None
