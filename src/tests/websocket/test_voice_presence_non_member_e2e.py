"""Real-server e2e for the voice and presence opcodes: non-members are denied.

The mocked suite (``test_opcode_manager_authorization.py``) stubs the owning
manager. These tests stand up the real voice module, the real signaling
manager, the real ``ServerManager`` membership graph and the real presence
manager, then drive the gateway dispatch path (``OpcodeHandler.handle``) as a
genuine non-member of the server.

Only the transport edge (the gateway dispatcher) is doubled, so a denial is
observed as "no state was written and nothing was fanned out". The permission
decision is the production one: ``voice.connect`` / channel visibility resolved
against real members.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.api as api
import src.api.websocket as websocket_module
import src.core.voice as voice
import src.core.voice.signaling as signaling
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.handlers import OpcodeHandler
from src.api.websocket.opcodes import GatewayCloseCode, GatewayOpcode
from src.core.presence import UserStatus
from src.core.voice.exceptions import PermissionDeniedError


def _voice_state_row(db, user_id: int):
    return db.fetch_one("SELECT * FROM voice_states WHERE user_id = ?", (user_id,))


@pytest.fixture
def realtime(
    db,
    auth_manager,
    server_manager,
    rel_manager,
    presence_manager,
    messaging_manager,
    three_users,
    session_manager,
    mock_auth_module,
    mock_websocket,
    connect_as,
    monkeypatch,
):
    """A real server whose owner + member may speak; the third user may not."""
    owner, member, outsider = three_users

    server = server_manager.create_server(owner.id, "Voice E2E server")
    server_manager.add_member(server.id, member.id)
    voice_channel = server_manager.create_channel(
        owner.id, server.id, "voice-chat", channel_type=server_manager.ChannelType.VOICE
    )
    text_channel = server_manager.create_channel(owner.id, server.id, "general")

    voice.setup(
        db,
        auth_module=auth_manager,
        servers_module=server_manager,
        relationships_module=rel_manager,
        presence_module=presence_manager,
    )
    signaling.setup(
        voice_module=voice,
        events_module=None,
        sfu_backend="aiortc",
        stun_urls=["stun:stun.example.invalid:3478"],
        turn_urls=[],
        turn_secret="",
        turn_ttl=3600,
    )

    # The handlers resolve the servers/messaging/auth boundary through src.api.
    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_servers", lambda: server_manager)
    monkeypatch.setattr(api, "get_messaging", lambda: messaging_manager)
    monkeypatch.setattr(api, "get_auth", lambda: auth_manager)

    dispatcher = GatewayDispatcher(session_manager=session_manager)
    handler = OpcodeHandler(
        session_manager=session_manager,
        auth_module=mock_auth_module,
        presence_module=presence_manager,
        servers_module=server_manager,
        dispatcher=dispatcher,
    )
    # The voice handler resolves its dispatcher through the gateway module
    # (``src.api.websocket.get_dispatcher``), so wire the same real dispatcher
    # there -- otherwise ``is_setup()`` is False and no fan-out is exercised.
    monkeypatch.setattr(websocket_module, "_dispatcher", dispatcher)
    monkeypatch.setattr(websocket_module, "_setup_complete", True)
    # Fan-out is observed through the handler, not the network.
    handler._presence_handler._dispatch_typing_event = _async_recorder()

    try:
        yield SimpleNamespace(
            owner=owner,
            member=member,
            outsider=outsider,
            server=server,
            voice_channel=voice_channel,
            text_channel=text_channel,
            handler=handler,
            dispatcher=dispatcher,
            conn_ws=mock_websocket,
            server_manager=server_manager,
            presence_manager=presence_manager,
            db=db,
            owner_conn=connect_as(owner.id),
            member_conn=connect_as(member.id),
            outsider_conn=connect_as(outsider.id),
        )
    finally:
        try:
            if voice.is_user_in_voice(outsider.id):
                voice.leave_channel(outsider.id)
        except Exception:
            pass
        for user in (owner, member):
            try:
                if voice.is_user_in_voice(user.id):
                    voice.leave_channel(user.id)
            except Exception:
                pass
        try:
            asyncio.run(signaling.close())
        except Exception:
            pass
        voice._manager = None
        voice._setup_complete = False


def _async_recorder():
    from unittest.mock import AsyncMock

    return AsyncMock(return_value=None)


# === VOICE_CONNECT =========================================================


@pytest.mark.asyncio
async def test_voice_connect_denied_for_non_member(realtime):
    result = await realtime.handler.handle(
        realtime.outsider_conn,
        GatewayOpcode.VOICE_CONNECT,
        {"channel_id": int(realtime.voice_channel.id)},
    )

    # The failure is surfaced rather than reported as an accepted join.
    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))
    assert voice.get_voice_state(realtime.outsider.id) is None
    assert _voice_state_row(realtime.db, realtime.outsider.id) is None

    # Non-vacuous: the opcode's own inner call refuses on permission grounds.
    with pytest.raises(PermissionDeniedError):
        voice.join_channel(realtime.outsider.id, int(realtime.voice_channel.id))


@pytest.mark.asyncio
async def test_voice_connect_allowed_for_member(realtime):
    result = await realtime.handler.handle(
        realtime.member_conn,
        GatewayOpcode.VOICE_CONNECT,
        {"channel_id": int(realtime.voice_channel.id)},
    )

    assert result == (None, None, None)
    assert voice.get_voice_state(realtime.member.id) is not None
    assert _voice_state_row(realtime.db, realtime.member.id) is not None


# === VOICE_SDP_OFFER =======================================================


@pytest.mark.asyncio
async def test_voice_sdp_offer_denied_for_non_member(realtime):
    result = await realtime.handler.handle(
        realtime.outsider_conn,
        GatewayOpcode.VOICE_SDP_OFFER,
        {"channel_id": int(realtime.voice_channel.id), "sdp": "v=0"},
    )

    assert result == (None, None, int(GatewayCloseCode.UNKNOWN_ERROR))
    assert signaling._get_manager()._connections.get(realtime.outsider.id) is None

    # Non-vacuous: the signaling entry point itself refuses for a non-member.
    with pytest.raises(PermissionDeniedError):
        await signaling.handle_sdp_offer_async(
            realtime.outsider.id, int(realtime.voice_channel.id), "v=0"
        )


@pytest.mark.asyncio
async def test_voice_sdp_offer_passes_permission_gate_for_member(realtime):
    """The same gate the opcode runs allows a real member through."""
    signaling._get_manager()._require_connect_permission(
        realtime.member.id, int(realtime.voice_channel.id)
    )


@pytest.mark.asyncio
async def test_view_only_member_is_denied_media_credentials(realtime, server_manager):
    """A member who may not ``voice.connect`` must not receive credentials.

    Regression: ``setup(voice_module=...)`` is handed the ``src.core.voice``
    MODULE in production, so the guard's ``getattr(module, ...)`` for the
    private permission helpers returned None and it returned early -- failing
    open. ``get_voice_channel`` only enforces ``channels.view``, so a view-only
    member was issued a session, a media token and TURN credentials anyway.

    The companion assertion pins that the denial is specifically
    ``voice.connect``: the same member can still see the channel.
    """
    everyone_role = next(
        role
        for role in server_manager.get_roles(realtime.owner.id, realtime.server.id)
        if role.is_default
    )
    server_manager.set_channel_override(
        realtime.owner.id,
        int(realtime.voice_channel.id),
        "role",
        everyone_role.id,
        deny={"voice.connect": True},
    )

    # channels.view still passes -- the channel is visible to this member.
    assert (
        voice.get_voice_channel(int(realtime.voice_channel.id), realtime.member.id)
        is not None
    )

    with pytest.raises(PermissionDeniedError):
        signaling.get_voice_server_info(
            realtime.member.id, int(realtime.voice_channel.id)
        )


# === VOICE_SPEAKING: relayed to the channel, never to a non-member ========


def _opcode_frames(conn_ws, opcode: int):
    """Every frame of ``opcode`` written to the transport so far."""
    frames = []
    for call in conn_ws.send_json.await_args_list:
        frame = call[0][0]
        if frame.get("op") == opcode:
            frames.append(frame)
    return frames


def _spy_raw_fanout(monkeypatch, realtime):
    """Record the recipient sets while still running the real fan-out."""
    calls = []
    original = realtime.dispatcher.dispatch_raw_to_users

    async def _spy(user_ids, opcode, data):
        calls.append((list(user_ids), opcode, data))
        return await original(user_ids, opcode, data)

    monkeypatch.setattr(realtime.dispatcher, "dispatch_raw_to_users", _spy)
    return calls


@pytest.mark.asyncio
async def test_voice_speaking_reaches_the_other_member(realtime, monkeypatch):
    """The indicator the client renders comes from the server, once, to peers.

    Regression: the server read only ``channel_id`` from the frame and never
    emitted opcode 25, so ``handleSpeakingUpdate`` on every client was dead
    code (findings ANNEX Z callback audit).
    """
    await realtime.handler.handle(
        realtime.owner_conn,
        GatewayOpcode.VOICE_CONNECT,
        {"channel_id": int(realtime.voice_channel.id)},
    )
    realtime.conn_ws.send_json.reset_mock()
    calls = _spy_raw_fanout(monkeypatch, realtime)

    result = await realtime.handler.handle(
        realtime.owner_conn,
        GatewayOpcode.VOICE_SPEAKING,
        {
            "channel_id": int(realtime.voice_channel.id),
            "speaking": True,
            "volume": 0.4,
        },
    )

    assert result == (None, None, None)
    # Exactly one target: the other member. Not the speaker, not the outsider.
    assert [targets for targets, _op, _d in calls] == [[int(realtime.member.id)]]
    payload = calls[0][2]
    assert payload == {
        "user_id": str(realtime.owner.id),
        "channel_id": str(int(realtime.voice_channel.id)),
        "speaking": True,
        "volume": 0.4,
    }

    # ...and it really went out on the transport, as opcode 25.
    frames = _opcode_frames(realtime.conn_ws, int(GatewayOpcode.VOICE_SPEAKING))
    assert len(frames) == 1
    assert frames[0]["d"] == payload


@pytest.mark.asyncio
async def test_voice_speaking_from_a_non_member_is_not_relayed(realtime, monkeypatch):
    """A non-member cannot inject a speaking frame into the channel."""
    calls = _spy_raw_fanout(monkeypatch, realtime)

    result = await realtime.handler.handle(
        realtime.outsider_conn,
        GatewayOpcode.VOICE_SPEAKING,
        {
            "channel_id": int(realtime.voice_channel.id),
            "speaking": True,
            "volume": 1.0,
        },
    )

    assert result == (None, None, None)
    assert calls == []
    assert _opcode_frames(realtime.conn_ws, int(GatewayOpcode.VOICE_SPEAKING)) == []


# === Presence: TYPING_START / TYPING_STOP ==================================


@pytest.mark.asyncio
async def test_typing_start_denied_for_non_member(realtime):
    await realtime.handler.handle(
        realtime.outsider_conn,
        GatewayOpcode.TYPING_START,
        {"channel_id": int(realtime.text_channel.id)},
    )

    realtime.handler._presence_handler._dispatch_typing_event.assert_not_awaited()
    typing_ids = {
        indicator.user_id
        for indicator in realtime.presence_manager.get_typing_users(
            int(realtime.text_channel.id)
        )
    }
    assert realtime.outsider.id not in typing_ids


@pytest.mark.asyncio
async def test_typing_stop_denied_for_non_member(realtime):
    await realtime.handler.handle(
        realtime.outsider_conn,
        GatewayOpcode.TYPING_STOP,
        {"channel_id": int(realtime.text_channel.id)},
    )

    realtime.handler._presence_handler._dispatch_typing_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_typing_start_allowed_for_member(realtime):
    await realtime.handler.handle(
        realtime.owner_conn,
        GatewayOpcode.TYPING_START,
        {"channel_id": int(realtime.text_channel.id)},
    )

    dispatch = realtime.handler._presence_handler._dispatch_typing_event
    dispatch.assert_awaited()
    # Only the channel-visible member is in the audience.
    assert dispatch.await_args.kwargs["recipients"] == [realtime.member.id]


# === Presence: PRESENCE_UPDATE is self-scoped ==============================


@pytest.mark.asyncio
async def test_presence_update_ignores_a_foreign_user_id(realtime, monkeypatch):
    """A foreign ``user_id`` in the payload must not move another user."""
    # Keep the fan-out offline; the assertion is on persisted state.
    monkeypatch.setattr(
        realtime.handler._presence_handler,
        "_dispatch_presence_to_friends",
        _async_recorder(),
    )

    await realtime.handler.handle(
        realtime.outsider_conn,
        GatewayOpcode.PRESENCE_UPDATE,
        {"user_id": int(realtime.owner.id), "status": "dnd"},
    )

    outsider_presence = realtime.presence_manager.get_presence(realtime.outsider.id)
    assert outsider_presence.status == UserStatus.DND

    owner_presence = realtime.presence_manager.get_presence(realtime.owner.id)
    assert owner_presence is None or owner_presence.status != UserStatus.DND
