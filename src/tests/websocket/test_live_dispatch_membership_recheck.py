"""LIVE gateway dispatch must re-check channel/DM access per event.

RESUME replay re-checks a queued private frame against the viewer's CURRENT
membership (``GatewayDispatcher._replay_scope_authorized``). The LIVE fan-out
path had no equivalent gate: it trusted the producer's ``user_ids`` audience,
which is resolved (and often memoized) *before* the send. A viewer who loses
``channels.view`` / DM participation after that resolution but before dispatch
still received the private frame on their open socket -- a real cross-user
leak, asymmetric with the RESUME path.

These tests prove the leak (a lost-access viewer receives private content) and
that the per-dispatch fail-closed gate removes it without silencing an
authorized viewer.
"""

import pytest
from unittest.mock import Mock, patch

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent


VIEWER = 42
OTHER = 43
SERVER_ID = 7
CHANNEL_ID = 8
DM_ID = 99


def _connect(session_manager, mock_websocket, user_id):
    conn = Connection(
        websocket=mock_websocket,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    session_manager.create_session(
        conn, user_id=user_id, intents=int(GatewayIntent.all_intents())
    )
    return conn


def _sent(conn):
    return [call.args[0] for call in conn.websocket.send_json.call_args_list]


def _channel_message(content):
    """A server-channel MESSAGE_CREATE carrying the real routing scope."""
    return Event(
        event_type=EventType.MESSAGE_CREATE,
        data={
            "id": "500",
            "channel_id": str(CHANNEL_ID),
            "server_id": str(SERVER_ID),
            "author_id": "7",
            "content": content,
        },
        server_id=SERVER_ID,
        channel_id=CHANNEL_ID,
    )


def _dm_message_via_alias(content):
    """DM producer that puts the conversation id only in ``d`` (no Event scope)."""
    return Event(
        event_type=EventType.MESSAGE_CREATE,
        data={
            "id": "501",
            "channel_id": str(DM_ID),
            "conversation_id": str(DM_ID),
            "author_id": "7",
            "content": content,
        },
    )


@pytest.mark.asyncio
async def test_live_dispatch_withholds_channel_event_after_access_loss(
    session_manager, mock_websocket
):
    """A viewer kicked from a channel must not receive a later private message."""
    viewer = _connect(session_manager, mock_websocket, VIEWER)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    servers = Mock()
    # Membership-agnostic existence probe: the channel is real, so a None
    # get_channel verdict is a genuine access revocation (not a missing row).
    servers.channel_exists = Mock(return_value=True)
    servers.get_channel = Mock(return_value=Mock(id=CHANNEL_ID))

    with patch("src.api.get_servers", return_value=servers), patch(
        "src.api.get_messaging", return_value=None
    ):
        # While authorized the frame is delivered (also proves non-vacuity).
        assert await dispatcher.dispatch_event(
            _channel_message("visible"), [VIEWER]
        ) == 1
        assert any(
            f.get("t") == "MESSAGE_CREATE" for f in _sent(viewer)
        ), _sent(viewer)
        viewer.websocket.send_json.reset_mock()

        # Access revoked while the socket stays open.
        servers.get_channel.return_value = None
        count = await dispatcher.dispatch_event(
            _channel_message("private-after-revoke"), [VIEWER]
        )

    assert count == 0, "lost-access viewer still received the private event"
    wire = [str(f) for f in _sent(viewer)]
    assert not any("private-after-revoke" in f for f in wire), wire


@pytest.mark.asyncio
async def test_live_dispatch_filters_only_revoked_viewer_in_shared_audience(
    session_manager, mock_websocket
):
    """Cross-user proof: a stale audience with one revoked viewer leaks only
    to that viewer; the still-authorized viewer must keep receiving events."""
    # Each connection needs its own transport double so per-recipient frames
    # can be attributed to the right socket.
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    def _ws():
        ws = MagicMock()
        ws.client = SimpleNamespace(host="127.0.0.1")
        ws.headers = {"User-Agent": "TestClient/1.0"}
        ws.send_json = AsyncMock()
        ws.send_bytes = AsyncMock()
        ws.close = AsyncMock()
        return ws

    revoked = _connect(session_manager, _ws(), VIEWER)
    allowed = _connect(session_manager, _ws(), OTHER)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    servers = Mock()
    # A single explicit audience contains both users; only OTHER still has
    # channels.view on the channel. The channel itself exists for everyone.
    servers.channel_exists = Mock(return_value=True)
    servers.get_channel = Mock(
        side_effect=lambda cid, uid: Mock(id=cid) if uid == OTHER else None
    )

    with patch("src.api.get_servers", return_value=servers), patch(
        "src.api.get_messaging", return_value=None
    ):
        count = await dispatcher.dispatch_event(
            _channel_message("restricted content"), [VIEWER, OTHER]
        )

    assert count == 1, count
    allowed_wire = [str(f) for f in _sent(allowed)]
    revoked_wire = [str(f) for f in _sent(revoked)]
    assert any("restricted content" in f for f in allowed_wire), allowed_wire
    assert not any("restricted content" in f for f in revoked_wire), revoked_wire


@pytest.mark.asyncio
async def test_live_dispatch_withholds_dm_event_after_participant_removal(
    session_manager, mock_websocket
):
    """A removed DM participant must not receive a conversation-scoped event
    whose producer put the conversation id only in ``d`` (the alias case)."""
    viewer = _connect(session_manager, mock_websocket, VIEWER)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    messaging = Mock()
    messaging.is_participant = Mock(return_value=False)

    with patch("src.api.get_servers", return_value=None), patch(
        "src.api.get_messaging", return_value=messaging
    ):
        count = await dispatcher.dispatch_event(
            _dm_message_via_alias("dm secret"), [VIEWER]
        )

    assert count == 0, "removed DM participant still received the private event"
    wire = [str(f) for f in _sent(viewer)]
    assert not any("dm secret" in f for f in wire), wire


@pytest.mark.asyncio
async def test_live_dispatch_keeps_dm_event_when_participant_retained(
    session_manager, mock_websocket
):
    """Non-vacuity: a still-participating viewer must still get the frame."""
    viewer = _connect(session_manager, mock_websocket, VIEWER)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    messaging = Mock()
    messaging.is_participant = Mock(return_value=True)

    with patch("src.api.get_servers", return_value=None), patch(
        "src.api.get_messaging", return_value=messaging
    ):
        count = await dispatcher.dispatch_event(
            _dm_message_via_alias("still visible"), [VIEWER]
        )

    assert count == 1, count
    assert any("still visible" in str(f) for f in _sent(viewer)), _sent(viewer)


@pytest.mark.asyncio
async def test_live_dispatch_scope_less_event_unaffected(
    session_manager, mock_websocket
):
    """User-scoped/scope-less frames (no channel/conversation id) must not be
    blocked by the new gate -- it only applies to private channel/DM events."""
    viewer = _connect(session_manager, mock_websocket, VIEWER)
    dispatcher = GatewayDispatcher(session_manager=session_manager)

    alert = Event(
        event_type=EventType.SECURITY_ALERT,
        data={"user_id": str(VIEWER), "action": "password_changed"},
    )
    servers = Mock()
    servers.get_channel = Mock(return_value=None)
    with patch("src.api.get_servers", return_value=servers), patch(
        "src.api.get_messaging", return_value=None
    ):
        count = await dispatcher.dispatch_event(alert, [VIEWER])

    assert count == 1, count
    assert any(f.get("t") == "SECURITY_ALERT" for f in _sent(viewer)), _sent(viewer)
