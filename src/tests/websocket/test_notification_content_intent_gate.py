"""Notification frames must not embed message previews without MESSAGE_CONTENT.

``NOTIFICATION_CREATE`` is dispatched with no scope and no base message
intent -- the router's default-allow delivers it to every connection -- yet
its payload carries the mentioned message's body in ``content`` and
``content_preview``. Without a payload-level gate at the wire boundary, a
connection that never declared the privileged MESSAGE_CONTENT intent still
receives the private text. This is the same class of leak as the already
fixed MESSAGE_BOOKMARK_* / GUILD_MEMBERS_CHUNK embedded-body gates.

The gate lives in ``Connection.send_json`` so it covers live fan-out,
single-connection dispatch, raw frames and RESUME replay alike. These tests
are non-vacuous: a positive control proves the frame is still delivered
intact when MESSAGE_CONTENT is declared, and the negative cases prove only
the embedded preview is withheld, not the notification itself.
"""

import pytest

from src.api.websocket.connection import Connection, ConnectionState
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent

pytestmark = [pytest.mark.websocket, pytest.mark.security]

BODY = "private mentioned message body"


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
        connection_id="notification-gate-conn",
        state=ConnectionState.READY,
        user_id=7,
        intents=intents,
    )
    return conn, socket


def _frame():
    """Wire shape emitted by ``_create_notification`` (scope-less)."""
    return {
        "op": 0,
        "t": EventType.NOTIFICATION_CREATE.value,
        "s": 7,
        "d": {
            "id": "5",
            "type": "mention",
            "title": "You were mentioned",
            "content": BODY,
            "content_preview": BODY,
            "read": False,
            "created_at": 1,
            "link": "/channels/10/20/99",
            "sender_id": "3",
        },
    }


@pytest.mark.asyncio
async def test_notification_preview_withheld_without_message_content():
    """A base message intent authorizes delivery, never the private preview."""
    conn, socket = _connection(int(GatewayIntent.GUILD_MESSAGES))
    await conn.send_json(_frame())

    assert len(socket.frames) == 1  # non-vacuous: the frame was still sent
    frame = socket.frames[0]
    # The base notification metadata survives untouched ...
    assert frame["d"]["id"] == "5"
    assert frame["d"]["title"] == "You were mentioned"
    assert frame["d"]["link"] == "/channels/10/20/99"
    # ... only the embedded private body is scrubbed.
    assert frame["d"]["content"] is None
    assert frame["d"]["content_preview"] is None


@pytest.mark.asyncio
async def test_notification_preview_withheld_even_with_no_intents():
    """NOTIFICATION_CREATE is default-allowed; no MESSAGE_CONTENT still scrubs."""
    conn, socket = _connection(0)
    await conn.send_json(_frame())

    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["content"] is None
    assert socket.frames[0]["d"]["content_preview"] is None


@pytest.mark.asyncio
async def test_notification_preview_delivered_when_intent_declared():
    """Positive control: MESSAGE_CONTENT restores the preview verbatim."""
    mask = int(GatewayIntent.GUILD_MESSAGES | GatewayIntent.MESSAGE_CONTENT)
    conn, socket = _connection(mask)
    await conn.send_json(_frame())

    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["content"] == BODY
    assert socket.frames[0]["d"]["content_preview"] == BODY


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_mask", [0, None, -1, True, "garbage", 2**40])
async def test_notification_preview_fails_closed_on_invalid_or_missing_mask(bad_mask):
    """No/absent/corrupt masks never grant the privileged preview read."""
    conn, socket = _connection(bad_mask)
    await conn.send_json(_frame())

    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["content"] is None
    assert socket.frames[0]["d"]["content_preview"] is None


@pytest.mark.asyncio
async def test_end_to_end_dispatch_scrubs_notification_preview():
    """The gateway dispatch path (not just a direct send) enforces the gate."""
    conn, socket = _connection(int(GatewayIntent.GUILD_MESSAGES))
    event = Event(event_type=EventType.NOTIFICATION_CREATE, data=_frame()["d"])
    dispatcher = GatewayDispatcher(session_manager=None)

    result = await dispatcher.dispatch_to_connection(conn, event)

    assert result is True  # default-allow delivered the base notification
    assert len(socket.frames) == 1
    assert socket.frames[0]["d"]["title"] == "You were mentioned"
    assert socket.frames[0]["d"]["content"] is None
    assert socket.frames[0]["d"]["content_preview"] is None
