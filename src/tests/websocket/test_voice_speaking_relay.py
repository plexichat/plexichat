"""The VOICE_SPEAKING indicator must reach the other members (opcode 25 relay).

The client has always sent ``{channel_id, speaking, volume}`` on opcode 25 and
bound ``handleSpeakingUpdate`` to opcode 25 *inbound* -- but the server read only
``channel_id`` and never emitted the frame, so the peer speaking indicator could
never fire (see the callback audit in findings ANNEX Z).

These tests pin both halves:

- ``GatewayDispatcher.dispatch_raw_to_users`` delivers a raw, non-DISPATCH frame
  to every connection of the target users and reports the count.
- ``VoiceHandler.handle_voice_speaking`` relays a validated, clamped indicator
  to the channel's other visible members -- never to the sender, never to a
  user who is not in that voice channel.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.handlers.voice import VoiceHandler
from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode


def _connection(session_manager, user_id: int) -> Connection:
    """A READY connection on its own transport double, for send assertions."""
    conn = Connection(
        websocket=MagicMock(),
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    conn.websocket.send_json = AsyncMock()
    session_manager.add_connection(conn)
    session = session_manager.create_session(conn, user_id=user_id, intents=513)
    conn.set_identified(user_id, session.session_id, 513)
    return conn


# === dispatcher: raw fan-out ===============================================


@pytest.mark.asyncio
async def test_dispatch_raw_to_users_sends_one_frame_per_connection(
    dispatcher, session_manager
) -> None:
    first = _connection(session_manager, 111)
    second = _connection(session_manager, 222)

    payload = {"user_id": "111", "channel_id": "9", "speaking": True, "volume": 0.5}
    sent = await dispatcher.dispatch_raw_to_users(
        [111, 222], GatewayOpcode.VOICE_SPEAKING, payload
    )

    assert sent == 2
    for conn in (first, second):
        call_args = conn.websocket.send_json.call_args[0][0]
        assert call_args["op"] == int(GatewayOpcode.VOICE_SPEAKING)
        assert call_args["d"] == payload


@pytest.mark.asyncio
async def test_dispatch_raw_to_users_with_no_targets_sends_nothing(
    dispatcher, session_manager
) -> None:
    conn = _connection(session_manager, 111)

    assert (
        await dispatcher.dispatch_raw_to_users(
            [], GatewayOpcode.VOICE_SPEAKING, {"speaking": True}
        )
        == 0
    )
    conn.websocket.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_raw_to_users_ignores_users_without_connections(
    dispatcher, session_manager
) -> None:
    """A target that is not connected is skipped, not an error."""
    conn = _connection(session_manager, 111)

    assert (
        await dispatcher.dispatch_raw_to_users(
            [111, 999999], GatewayOpcode.VOICE_SPEAKING, {"speaking": False}
        )
        == 1
    )
    conn.websocket.send_json.assert_awaited()


# === handler: relay to the channel's other members =========================


def _servers_double(*, members):
    """Servers double honouring the exclusion the handler passes down."""
    servers = MagicMock()
    servers.get_channel.return_value = SimpleNamespace(server_id=7)
    servers.get_member_user_ids.side_effect = lambda server_id, exclude_user_id=None: [
        member for member in members if member != exclude_user_id
    ]
    return servers


def _wire_dispatcher(monkeypatch, dispatcher_double):
    monkeypatch.setattr("src.api.websocket.is_setup", lambda: True)
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher_double)


@pytest.fixture
def in_voice(monkeypatch):
    """The caller is bound to voice channel 55."""
    import src.core.voice as voice
    import src.core.voice.signaling as signaling

    monkeypatch.setattr(
        voice,
        "get_voice_state",
        lambda user_id: SimpleNamespace(
            channel_id=55, self_mute=False, self_deaf=False
        ),
    )
    monkeypatch.setattr(signaling, "record_activity", lambda user_id: None)


@pytest.mark.asyncio
async def test_speaking_is_relayed_to_the_other_members(
    monkeypatch, authenticated_connection, in_voice
) -> None:
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock(return_value=1)
    dispatcher_double.dispatch_event = AsyncMock(return_value=1)
    _wire_dispatcher(monkeypatch, dispatcher_double)

    # Sender (12345) plus two other visible members.
    servers = _servers_double(members=[12345, 777, 888])
    handler = VoiceHandler(servers_module=servers)

    await handler.handle_voice_speaking(
        authenticated_connection,
        {"channel_id": 55, "speaking": True, "volume": 0.25},
    )

    dispatcher_double.dispatch_raw_to_users.assert_awaited_once()
    args = dispatcher_double.dispatch_raw_to_users.await_args
    targets, opcode, payload = args.args
    # The sender is excluded; only the other members are addressed.
    assert sorted(targets) == [777, 888]
    assert int(opcode) == int(GatewayOpcode.VOICE_SPEAKING)
    assert payload == {
        "user_id": "12345",
        "channel_id": "55",
        "speaking": True,
        "volume": 0.25,
    }


@pytest.mark.asyncio
async def test_speaking_volume_is_clamped(
    monkeypatch, authenticated_connection, in_voice
) -> None:
    """A client cannot render arbitrary values in other members' UIs."""
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock(return_value=1)
    dispatcher_double.dispatch_event = AsyncMock(return_value=1)
    _wire_dispatcher(monkeypatch, dispatcher_double)

    handler = VoiceHandler(servers_module=_servers_double(members=[12345, 777]))

    for raw, expected in [(9.5, 1.0), (-3, 0.0), ("loud", 0.0), (None, 0.0)]:
        dispatcher_double.dispatch_raw_to_users.reset_mock()
        await handler.handle_voice_speaking(
            authenticated_connection,
            {"channel_id": 55, "speaking": True, "volume": raw},
        )
        payload = dispatcher_double.dispatch_raw_to_users.await_args.args[2]
        assert payload["volume"] == expected, raw
        assert payload["speaking"] is True


@pytest.mark.asyncio
async def test_speaking_is_not_relayed_by_a_user_in_another_channel(
    monkeypatch, authenticated_connection
) -> None:
    """No relay, no state update: the channel binding is authoritative."""
    import src.core.voice as voice

    monkeypatch.setattr(
        voice, "get_voice_state", lambda user_id: SimpleNamespace(channel_id=999)
    )
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock()
    dispatcher_double.dispatch_event = AsyncMock()
    _wire_dispatcher(monkeypatch, dispatcher_double)

    handler = VoiceHandler(servers_module=_servers_double(members=[12345, 777]))

    result = await handler.handle_voice_speaking(
        authenticated_connection, {"channel_id": 55, "speaking": True, "volume": 1}
    )

    assert result == (None, None, None)
    dispatcher_double.dispatch_raw_to_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_speaking_without_a_server_does_not_relay(
    monkeypatch, authenticated_connection, in_voice
) -> None:
    """A channel the server module cannot resolve has no audience."""
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock()
    dispatcher_double.dispatch_event = AsyncMock()
    _wire_dispatcher(monkeypatch, dispatcher_double)

    servers = MagicMock()
    servers.get_channel.return_value = None  # not resolvable for this caller
    handler = VoiceHandler(servers_module=servers)

    await handler.handle_voice_speaking(
        authenticated_connection, {"channel_id": 55, "speaking": True, "volume": 1}
    )

    dispatcher_double.dispatch_raw_to_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_speaking_is_not_relayed_when_the_sender_is_out_of_voice(
    monkeypatch, authenticated_connection
) -> None:
    import src.core.voice as voice

    monkeypatch.setattr(voice, "get_voice_state", lambda user_id: None)
    dispatcher_double = MagicMock()
    dispatcher_double.dispatch_raw_to_users = AsyncMock()
    dispatcher_double.dispatch_event = AsyncMock()
    _wire_dispatcher(monkeypatch, dispatcher_double)

    handler = VoiceHandler(servers_module=_servers_double(members=[12345, 777]))

    result = await handler.handle_voice_speaking(
        authenticated_connection, {"channel_id": 55, "speaking": True, "volume": 1}
    )

    assert result == (None, None, None)
    dispatcher_double.dispatch_raw_to_users.assert_not_awaited()


@pytest.mark.asyncio
async def test_speaking_still_negative_acks_a_malformed_channel(
    monkeypatch, authenticated_connection
) -> None:
    handler = VoiceHandler(servers_module=_servers_double(members=[12345]))

    result = await handler.handle_voice_speaking(
        authenticated_connection, {"channel_id": "not-a-channel", "speaking": True}
    )

    assert result == (None, None, int(GatewayCloseCode.DECODE_ERROR))
