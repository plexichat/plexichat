"""Regression coverage for the SFU score-notification -> quality-store bridge.

``mediasoup_ws`` receives ``producerScore`` / ``consumerScore`` notifications
whose identifiers are SFU-scoped (``voice_<channel_id>`` / ``user_<user_id>``),
while ``QualityMixin.update_quality_hint`` keys everything by the *canonical*
numeric channel and user ids.

Regression: the adapter forwarded the raw SFU ids, so ``int("voice_123")``
raised ``ValueError`` inside the mixin. That exception escaped the receive loop
in ``_receive_messages``, which then logged a receive error and dropped the
peer's SFU connection -- killing the media session on a routine score update.
These tests pin the translation and the error containment.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.voice.signaling.sfu.mediasoup_ws import (
    MediasoupWSAdapter,
    PeerConnection,
)
from src.core.voice.signaling.sfu.quality import (
    canonical_channel_id,
    canonical_user_id,
)


def _adapter(mixin: Mock) -> MediasoupWSAdapter:
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._quality_mixin = mixin
    return adapter


def _connection(
    room_id: str = "voice_123", peer_id: str = "user_456"
) -> PeerConnection:
    return PeerConnection(peer_id=peer_id, room_id=room_id, websocket=Mock())


# === Identifier mapping ====================================================


@pytest.mark.parametrize(
    "room_id,expected",
    [
        ("voice_123", 123),
        ("voice_1", 1),
        (123, 123),
        ("room-1", None),
        ("voice_", None),
        ("voice_abc", None),
        ("voice_0", None),
        ("voice_-5", None),
        (None, None),
        (True, None),
    ],
)
def test_canonical_channel_id(room_id, expected):
    assert canonical_channel_id(room_id) == expected


@pytest.mark.parametrize(
    "peer_id,expected",
    [
        ("user_456", 456),
        ("user_1", 1),
        (456, 456),
        ("peer-1", None),
        ("user_", None),
        ("user_xyz", None),
        ("user_0", None),
        (None, None),
        (False, None),
    ],
)
def test_canonical_user_id(peer_id, expected):
    assert canonical_user_id(peer_id) == expected


# === Score notifications ===================================================


@pytest.mark.asyncio
async def test_producer_score_translates_sfu_ids_to_canonical_key():
    mixin = Mock()
    adapter = _adapter(mixin)

    await adapter._handle_notification(
        _connection(),
        {
            "method": "producerScore",
            "data": {
                "peerId": "user_456",
                "score": 8,
                "bitrate": 90000,
                "packetLoss": 1.5,
                "jitter": 12.0,
            },
        },
    )

    mixin.update_quality_hint.assert_called_once_with(
        123, 456, bitrate=90000, packet_loss=1.5, jitter=12.0, score=8
    )


@pytest.mark.asyncio
async def test_consumer_score_maps_round_trip_time():
    mixin = Mock()
    adapter = _adapter(mixin)

    await adapter._handle_notification(
        _connection(),
        {
            "method": "consumerScore",
            "data": {
                "peerId": "user_456",
                "score": 7,
                "bitrate": 64000,
                "packetLoss": 0.0,
                "jitter": 0.0,
                "roundTripTime": 42,
            },
        },
    )

    mixin.update_quality_hint.assert_called_once_with(
        123,
        456,
        bitrate=64000,
        packet_loss=0.0,
        jitter=0.0,
        round_trip_time=42,
        score=7,
    )


@pytest.mark.asyncio
async def test_notification_peer_id_overrides_connection_peer():
    mixin = Mock()
    adapter = _adapter(mixin)

    await adapter._handle_notification(
        _connection(peer_id="user_1"),
        {"method": "producerScore", "data": {"peerId": "user_2", "score": 1}},
    )

    assert mixin.update_quality_hint.call_args.args == (123, 2)


@pytest.mark.asyncio
async def test_unmappable_sfu_ids_are_not_reported():
    """An SFU peer id that is not ``user_<id>`` cannot key the store."""
    mixin = Mock()
    adapter = _adapter(mixin)

    await adapter._handle_notification(
        _connection(room_id="room-1", peer_id="peer-1"),
        {"method": "producerScore", "data": {"score": 5}},
    )

    mixin.update_quality_hint.assert_not_called()


@pytest.mark.asyncio
async def test_quality_mixin_failure_does_not_propagate():
    """A failing metric update must never escape into the receive loop."""
    mixin = Mock()
    mixin.update_quality_hint.side_effect = ValueError("boom")
    adapter = _adapter(mixin)

    # No exception: the session stays alive.
    await adapter._handle_notification(
        _connection(), {"method": "producerScore", "data": {"score": 1}}
    )

    mixin.update_quality_hint.assert_called_once()


@pytest.mark.asyncio
async def test_unwired_quality_mixin_is_a_noop():
    adapter = _adapter(None)

    await adapter._handle_notification(
        _connection(), {"method": "producerScore", "data": {"score": 1}}
    )


# === Receive-loop containment ==============================================


@pytest.mark.asyncio
async def test_handle_message_contains_a_failing_notification_handler(monkeypatch):
    """A handler exception must not escape and kill the receive loop."""
    mixin = Mock()
    adapter = _adapter(mixin)
    monkeypatch.setattr(
        adapter, "_handle_notification", AsyncMock(side_effect=RuntimeError("bad"))
    )

    message = json.dumps({"notification": True, "method": "producerScore", "data": {}})
    # No exception: ``_receive_messages`` keeps iterating.
    await adapter._handle_message(_connection(), message)


@pytest.mark.asyncio
async def test_handle_message_contains_a_failing_server_request_handler(monkeypatch):
    mixin = Mock()
    adapter = _adapter(mixin)
    monkeypatch.setattr(
        adapter, "_handle_server_request", AsyncMock(side_effect=RuntimeError("bad"))
    )

    message = json.dumps({"request": True, "method": "newConsumer", "data": {}})
    await adapter._handle_message(_connection(), message)
