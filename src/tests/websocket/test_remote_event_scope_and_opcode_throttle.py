"""Regression guards for two gateway hardening gaps.

* A cross-worker (Valkey pub/sub) event lost its ``server_id``/``channel_id``
  on serialization, so the intent router classified every remote event as
  guild-scoped. A client holding only GUILD_MESSAGES received DM events that
  the publishing worker correctly withheld from it.
* Several DB-writing / fan-out mutating opcodes had no inbound throttle at
  all, letting an identified socket loop them for unbounded work.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.websocket.connection import Connection
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.handlers import INBOUND_RATE_LIMITS, OpcodeHandler
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager
from src.core.events.types import GatewayIntent


def _identified(session_manager, user_id, intents):
    """Create a READY connection with its own isolated websocket double."""
    ws = MagicMock()
    ws.client = SimpleNamespace(host="127.0.0.1")
    ws.headers = {}
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.close = AsyncMock()
    conn = Connection(
        websocket=ws,
        connection_id=session_manager.generate_connection_id(),
        heartbeat_interval_ms=45000,
    )
    session_manager.add_connection(conn)
    session_manager.create_session(conn, user_id=user_id, intents=intents)
    return conn, ws


# === Cross-worker scope restoration =========================================


@pytest.mark.asyncio
async def test_remote_dm_is_withheld_from_guild_only_intent(session_manager):
    """A DM event from a sibling worker must not reach a guild-only client."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, guild_ws = _identified(
        session_manager, 111, int(GatewayIntent.GUILD_MESSAGES)
    )
    _, dm_ws = _identified(
        session_manager, 222, int(GatewayIntent.DIRECT_MESSAGES)
    )

    payload = {"id": "1", "channel_id": "999", "content": "private-dm-body"}
    sent = await dispatcher.dispatch_remote_event(
        "MESSAGE_CREATE", payload, [111, 222]
    )

    assert sent == 1, "only the DIRECT_MESSAGES client should receive the DM"
    guild_ws.send_json.assert_not_called()
    dm_ws.send_json.assert_called_once()
    assert dm_ws.send_json.call_args.args[0]["d"]["content"] == "private-dm-body"


@pytest.mark.asyncio
async def test_remote_guild_message_reaches_guild_intent(session_manager):
    """Guild scope is recovered from server_id/guild_id in the payload."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, guild_ws = _identified(
        session_manager, 333, int(GatewayIntent.GUILD_MESSAGES)
    )

    payload = {
        "id": "2",
        "channel_id": "999",
        "guild_id": "7",
        "content": "guild-visible",
    }
    sent = await dispatcher.dispatch_remote_event("MESSAGE_CREATE", payload, [333])

    assert sent == 1
    guild_ws.send_json.assert_called_once()
    assert guild_ws.send_json.call_args.args[0]["d"]["content"] == "guild-visible"


@pytest.mark.parametrize(
    "data, allowed_intent, denied_intent",
    [
        (
            {"channel_id": "5", "guild_id": "7"},
            GatewayIntent.GUILD_MESSAGE_TYPING,
            GatewayIntent.DIRECT_MESSAGE_TYPING,
        ),
        (
            {"channel_id": "5"},
            GatewayIntent.DIRECT_MESSAGE_TYPING,
            GatewayIntent.GUILD_MESSAGE_TYPING,
        ),
    ],
)
@pytest.mark.asyncio
async def test_remote_typing_scope_is_preserved(
    session_manager, data, allowed_intent, denied_intent
):
    """Typing payloads carry only the legacy ``guild_id`` for guild scope."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, allowed_ws = _identified(session_manager, 444, int(allowed_intent))
    _, denied_ws = _identified(session_manager, 555, int(denied_intent))

    sent = await dispatcher.dispatch_remote_event(
        "TYPING_START", data, [444, 555]
    )

    assert sent == 1
    allowed_ws.send_json.assert_called_once()
    denied_ws.send_json.assert_not_called()


@pytest.mark.asyncio
async def test_remote_event_with_unparseable_scope_does_not_crash(session_manager):
    """A malformed scope never raises out of the pub/sub receive path."""
    dispatcher = GatewayDispatcher(session_manager=session_manager)
    _, ws = _identified(
        session_manager, 666, int(GatewayIntent.GUILD_MESSAGES)
    )

    sent = await dispatcher.dispatch_remote_event(
        "MESSAGE_CREATE", {"channel_id": object()}, [666]
    )
    assert sent == 1
    ws.send_json.assert_called_once()


# === Opcode throttles =======================================================


def test_expensive_mutating_opcodes_are_throttled():
    """DB-writing / fan-out opcodes must all carry an inbound ceiling."""
    for op in (
        GatewayOpcode.VOICE_STATE_UPDATE,
        GatewayOpcode.VOICE_DISCONNECT,
        GatewayOpcode.TYPING_STOP,
        GatewayOpcode.VOICE_QUALITY,
    ):
        assert int(op) in INBOUND_RATE_LIMITS


@pytest.mark.asyncio
async def test_voice_state_update_loop_is_capped_before_dispatch(monkeypatch):
    """Over-budget VOICE_STATE_UPDATE frames never reach the handler."""
    handler = OpcodeHandler(session_manager=SessionManager())
    op_mock = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(
        handler._voice_handler, "handle_voice_state_update", op_mock
    )

    conn = SimpleNamespace(
        connection_id="throttle-conn",
        _inbound_rate_buckets={},
        is_authenticated=True,
        is_selftest=False,
        user_id=42,
    )
    budget = INBOUND_RATE_LIMITS[int(GatewayOpcode.VOICE_STATE_UPDATE)][0]
    for _ in range(budget + 5):
        await handler.handle(
            conn,
            int(GatewayOpcode.VOICE_STATE_UPDATE),
            {"channel_id": 5},
        )

    assert op_mock.await_count == budget


@pytest.mark.parametrize("op", [GatewayOpcode.IDENTIFY, GatewayOpcode.RESUME])
def test_auth_opcodes_are_throttled(op):
    """IDENTIFY/RESUME verify tickets and (for RESUME) replay the event buffer."""
    assert int(op) in INBOUND_RATE_LIMITS


@pytest.mark.asyncio
async def test_resume_loop_is_capped_before_dispatch(monkeypatch):
    """Over-budget RESUME frames never reach the ticket-verification path."""
    handler = OpcodeHandler(session_manager=SessionManager())
    resume_mock = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(handler._connection_handler, "handle_resume", resume_mock)

    conn = SimpleNamespace(
        connection_id="resume-throttle-conn",
        _inbound_rate_buckets={},
        is_authenticated=False,
        is_selftest=False,
        user_id=None,
    )
    budget = INBOUND_RATE_LIMITS[int(GatewayOpcode.RESUME)][0]
    for _ in range(budget + 5):
        await handler.handle(
            conn,
            int(GatewayOpcode.RESUME),
            {"token": "wst.1.abcdef", "session_id": "deadbeef", "seq": 0},
        )

    assert resume_mock.await_count == budget


@pytest.mark.asyncio
async def test_resume_passes_peer_context_to_token_verification(monkeypatch):
    """RESUME must feed the peer IP/UA so per-IP token-verify limits apply."""
    handler = OpcodeHandler(session_manager=SessionManager())
    verify = AsyncMock(return_value=None)
    monkeypatch.setattr(handler._connection_handler, "_verify_token", verify)

    conn = SimpleNamespace(
        is_selftest=False,
        websocket=SimpleNamespace(
            client=SimpleNamespace(host="203.0.113.9"),
            headers={"User-Agent": "test-client/1.0"},
        ),
    )
    await handler._connection_handler.handle_resume(
        conn,
        {
            "token": "wst.1.abcdef",
            "session_id": "deadbeef",
            "seq": 0,
        },
    )

    verify.assert_awaited_once()
    kwargs = verify.await_args.kwargs
    assert kwargs["ip_address"] == "203.0.113.9"
    assert kwargs["user_agent"] == "test-client/1.0"
    assert kwargs["is_selftest"] is False
