"""Trickle ICE candidates must reach the media plane, not just the manager.

``ICEMixin.handle_ice_candidate`` recorded a client candidate on the signaling
connection, but no adapter method existed to apply it, so the SFU peer
connection never learned the client's address. Loopback tests could not see
this: ICE still completes there via peer-reflexive discovery when the SFU
receives the client's checks first. Across a NAT the SFU has no candidate to
initiate a check *toward* the client, and connectivity depends on luck.

These tests pin the contract at both layers: the adapter maps a candidate onto
its peer connection, and the manager forwards one on ``handle_ice_candidate_async``
while preserving the sync method's record-only behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

aiortc = pytest.importorskip("aiortc")

from src.core.voice.signaling.manager.ice_mixin import ICEMixin  # noqa: E402
from src.core.voice.signaling.models import SignalingState  # noqa: E402
from src.core.voice.signaling.sfu.aiortc_adapter import (  # noqa: E402
    AiortcAdapter,
    AiortcPeer,
)

HOST_CANDIDATE = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"
SRFLX_CANDIDATE = (
    "candidate:2 1 udp 1694498815 203.0.113.50 12345 typ srflx "
    "raddr 192.168.1.100 rport 54321"
)


class _FakePC:
    """Just enough RTCPeerConnection for candidate handling."""

    def __init__(self):
        self.addIceCandidate = AsyncMock()


def _adapter_with_fake_peer(room_id="voice_11", peer_id="user_22"):
    adapter = AiortcAdapter()
    pc = _FakePC()
    adapter._rooms.setdefault(room_id, {})[peer_id] = AiortcPeer(peer_id=peer_id, pc=pc)
    return adapter, pc


@pytest.mark.asyncio
async def test_add_ice_candidate_maps_fields_onto_the_peer_connection():
    adapter, pc = _adapter_with_fake_peer()

    accepted = await adapter.add_ice_candidate(
        "voice_11", "user_22", SRFLX_CANDIDATE, "0", 0
    )

    assert accepted is True
    pc.addIceCandidate.assert_awaited_once()
    candidate = pc.addIceCandidate.await_args.args[0]
    assert candidate.foundation == "2"
    assert candidate.component == 1
    assert candidate.ip == "203.0.113.50"
    assert candidate.port == 12345
    assert candidate.priority == 1694498815
    assert candidate.protocol == "udp"
    assert candidate.type == "srflx"
    assert candidate.relatedAddress == "192.168.1.100"
    assert candidate.relatedPort == 54321
    assert candidate.sdpMid == "0"
    assert candidate.sdpMLineIndex == 0


@pytest.mark.asyncio
async def test_add_ice_candidate_refuses_an_unknown_peer():
    adapter, pc = _adapter_with_fake_peer()

    assert (
        await adapter.add_ice_candidate("voice_11", "user_ghost", HOST_CANDIDATE)
        is False
    )
    pc.addIceCandidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_ice_candidate_requires_an_m_line_binding():
    """aiortc raises without sdpMid/mLineIndex; refuse instead of guessing."""
    adapter, pc = _adapter_with_fake_peer()

    accepted = await adapter.add_ice_candidate("voice_11", "user_22", HOST_CANDIDATE)

    assert accepted is False
    pc.addIceCandidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_ice_candidate_contains_a_malformed_candidate():
    adapter, pc = _adapter_with_fake_peer()

    accepted = await adapter.add_ice_candidate(
        "voice_11", "user_22", "this is not a candidate", "0", 0
    )

    assert accepted is False
    pc.addIceCandidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_ice_candidate_contains_a_failing_peer_connection():
    adapter, pc = _adapter_with_fake_peer()
    pc.addIceCandidate = AsyncMock(side_effect=RuntimeError("pc is closed"))

    assert (
        await adapter.add_ice_candidate("voice_11", "user_22", HOST_CANDIDATE, "0", 0)
        is False
    )


# ---------------------------------------------------------------------------
# Manager layer
# ---------------------------------------------------------------------------


class _StubManager(ICEMixin):
    """Minimal host for the ICE mixin: connections + a spy SFU."""

    def __init__(self, sfu):
        self._connections = {}
        self._ice_manager = SimpleNamespace(
            add_candidate=lambda *a, **k: None, clear_candidates=lambda *a: None
        )
        self._sfu = sfu
        self.now = 1000

    def _get_timestamp(self) -> int:
        return self.now

    def _get_sfu(self):
        return self._sfu


def _connection(channel_id=5, state=SignalingState.CONNECTING):
    return SimpleNamespace(
        channel_id=channel_id,
        session_id="sess-1",
        state=state,
        ice_candidates=[],
        last_activity=0,
        sfu_room_id="voice_5",
        sfu_peer_id="user_7",
    )


@pytest.mark.asyncio
async def test_async_handler_forwards_the_candidate_to_the_sfu():
    sfu = SimpleNamespace(add_ice_candidate=AsyncMock(return_value=True))
    manager = _StubManager(sfu)
    manager._connections[7] = _connection()

    result = await manager.handle_ice_candidate_async(7, 5, HOST_CANDIDATE, "0", 0)

    assert result is True
    sfu.add_ice_candidate.assert_awaited_once_with(
        "voice_5", "user_7", HOST_CANDIDATE, "0", 0
    )
    # Recording still happened.
    assert len(manager._connections[7].ice_candidates) == 1
    assert manager._connections[7].state == SignalingState.CONNECTED


@pytest.mark.asyncio
async def test_sync_handler_records_without_touching_the_sfu():
    sfu = SimpleNamespace(add_ice_candidate=AsyncMock(return_value=True))
    manager = _StubManager(sfu)
    manager._connections[7] = _connection()

    result = manager.handle_ice_candidate(7, 5, HOST_CANDIDATE, "0", 0)

    assert result is True
    sfu.add_ice_candidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_handler_refuses_a_channel_mismatch():
    sfu = SimpleNamespace(add_ice_candidate=AsyncMock(return_value=True))
    manager = _StubManager(sfu)
    manager._connections[7] = _connection(channel_id=5)

    result = await manager.handle_ice_candidate_async(7, 999, HOST_CANDIDATE, "0", 0)

    assert result is False
    sfu.add_ice_candidate.assert_not_awaited()
    assert manager._connections[7].ice_candidates == []


@pytest.mark.asyncio
async def test_async_handler_tolerates_an_sfu_that_rejects_the_candidate():
    """A rejected candidate must not fail the client's trickle request."""
    sfu = SimpleNamespace(add_ice_candidate=AsyncMock(return_value=False))
    manager = _StubManager(sfu)
    manager._connections[7] = _connection()

    assert (
        await manager.handle_ice_candidate_async(7, 5, HOST_CANDIDATE, "0", 0) is True
    )


@pytest.mark.asyncio
async def test_async_handler_tolerates_an_sfu_that_raises():
    sfu = SimpleNamespace(add_ice_candidate=AsyncMock(side_effect=RuntimeError("boom")))
    manager = _StubManager(sfu)
    manager._connections[7] = _connection()

    assert (
        await manager.handle_ice_candidate_async(7, 5, HOST_CANDIDATE, "0", 0) is True
    )
