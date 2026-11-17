"""Regression tests for deep gateway hardening at the handler boundary.

Pins three attacks that must stay denied:

* an identified socket looping expensive fabric / media opcodes without ever
  hitting the inbound throttle (DB + SFU resource amplification),
* control characters / oversized values smuggled through the activity ``type``
  field of a PRESENCE_UPDATE (log + downstream broadcast injection),
* an event that is misclassified as a DM (no ``server_id``, but a
  ``channel_id``) bypassing its guild-scope intent, and invalid intent masks
  being treated as "all granted".
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.api.websocket.boundaries import (
    MAX_ACTIVITY_TYPE_LEN,
    validate_presence_payload,
)
from src.api.websocket.handlers import INBOUND_RATE_LIMITS, OpcodeHandler
from src.api.websocket.intents import filter_event_by_intents
from src.api.websocket.opcodes import GatewayOpcode
from src.api.websocket.session import SessionManager
from src.core.events.models import Event
from src.core.events.types import EventType, GatewayIntent
from src.core.security.hardening import HardeningViolation


def _throttled_connection():
    return SimpleNamespace(
        connection_id="deep-hardening-conn",
        _inbound_rate_buckets={},
        is_authenticated=True,
        is_selftest=False,
        user_id=42,
    )


# === Rate limiting per opcode ===============================================


@pytest.mark.asyncio
async def test_artifact_op_and_voice_connect_are_throttled(monkeypatch):
    """Expensive fabric/media opcodes must be rate-limited before dispatch."""
    for opcode in (
        GatewayOpcode.ARTIFACT_SUBSCRIBE,
        GatewayOpcode.ARTIFACT_OP,
        GatewayOpcode.SUM_SUBSCRIBE,
        GatewayOpcode.SUM_OP,
        GatewayOpcode.VOICE_CONNECT,
        GatewayOpcode.VOICE_SDP_OFFER,
        GatewayOpcode.VOICE_SDP_ANSWER,
        GatewayOpcode.VOICE_ICE_CANDIDATE,
    ):
        assert int(opcode) in INBOUND_RATE_LIMITS

    handler = OpcodeHandler(
        session_manager=SessionManager(),
        dispatcher=SimpleNamespace(_rate_limit_per_minute=None),
    )
    op_mock = AsyncMock(return_value=(None, None, None))
    connect_mock = AsyncMock(return_value=(None, None, None))
    monkeypatch.setattr(handler._artifact_handler, "handle_artifact_op", op_mock)
    monkeypatch.setattr(handler._voice_handler, "handle_voice_connect", connect_mock)

    op_budget = INBOUND_RATE_LIMITS[int(GatewayOpcode.ARTIFACT_OP)][0]
    conn = _throttled_connection()
    for _ in range(op_budget + 5):
        await handler.handle(
            conn,
            int(GatewayOpcode.ARTIFACT_OP),
            {"artifact_id": 10, "op": {"op_type": "edit"}},
        )
    assert op_mock.await_count == op_budget

    voice_budget = INBOUND_RATE_LIMITS[int(GatewayOpcode.VOICE_CONNECT)][0]
    conn = _throttled_connection()
    for _ in range(voice_budget + 5):
        await handler.handle(conn, int(GatewayOpcode.VOICE_CONNECT), {"channel_id": 55})
    assert connect_mock.await_count == voice_budget


# === Presence payload injection =============================================


def test_activity_type_is_bounded_and_control_characters_rejected():
    """The activity type must not carry control chars or unbounded text."""
    payload = {"status": "online", "activities": [{"type": "playing"}]}
    assert validate_presence_payload(payload)["activities"][0]["type"] == "playing"

    with pytest.raises(HardeningViolation):
        validate_presence_payload(
            {"status": "online", "activities": [{"type": "play\x1b[31ming"}]}
        )
    with pytest.raises(HardeningViolation):
        validate_presence_payload(
            {
                "status": "online",
                "activities": [{"type": "x" * (MAX_ACTIVITY_TYPE_LEN + 1)}],
            }
        )


# === Intent bypass ==========================================================


def test_artifact_event_requires_artifacts_intent_without_server_id():
    """A channel-bearing event with no server_id cannot dodge the intent."""
    event = Event(
        event_type=EventType.ARTIFACT_OP, data={}, server_id=None, channel_id=55
    )
    assert filter_event_by_intents(event, 0) is False
    assert filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGES)) is False
    assert filter_event_by_intents(event, int(GatewayIntent.ARTIFACTS)) is True


def test_dm_typing_keeps_its_own_intent():
    """The guild-intent guard must not break legitimate DM events."""
    event = Event(
        event_type=EventType.TYPING_START, data={}, server_id=None, channel_id=55
    )
    assert (
        filter_event_by_intents(event, int(GatewayIntent.GUILD_MESSAGE_TYPING)) is False
    )
    assert (
        filter_event_by_intents(event, int(GatewayIntent.DIRECT_MESSAGE_TYPING)) is True
    )


def test_invalid_intent_masks_fail_closed():
    """A corrupt/resumed mask must never expand event access."""
    event = Event(event_type=EventType.GUILD_CREATE, data={})
    assert filter_event_by_intents(event, int(GatewayIntent.GUILDS)) is True
    for invalid in (-1, True, 1 << 70):
        assert filter_event_by_intents(event, invalid) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", [-5, 0, 2**64, "not-an-id"])
async def test_typing_rejects_out_of_range_channel_ids(monkeypatch, bad_id):
    """Negative / oversized channel ids must not reach the DB or fan-out."""
    from unittest.mock import MagicMock

    import src.api as api
    import src.api.websocket.handlers.presence as hp

    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    presence = MagicMock()
    handler = hp.PresenceHandler(presence)
    handler._dispatch_typing_event = AsyncMock()

    conn = SimpleNamespace(
        is_authenticated=True, user_id=42, is_selftest=False, connection_id="bad-chan"
    )
    await handler.handle_typing_start(conn, {"channel_id": bad_id})
    await handler.handle_typing_stop(conn, {"channel_id": bad_id})

    presence.start_typing.assert_not_called()
    presence.stop_typing.assert_not_called()
    handler._dispatch_typing_event.assert_not_awaited()
