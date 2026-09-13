"""Janus media path: fail-closed without a gateway, end to end with a real one.

``test_sfu_janus_media.py`` asserts the *protocol exchange* against a mocked
Janus -- enough to pin our request shapes, but it cannot distinguish "our
publish/jsep framing is right" from "our opinion of the framing is right".

This module closes that gap in the two ways that are actually available:

1. **Always runs.** Against an endpoint that cannot answer, the adapter must
   raise rather than return an answer. That is the fail-closed property the
   whole ANNEX Z fix rests on: a dead gateway refuses the offer instead of
   handing the client a fabricated session.
2. **Real gateway (opt-in).** Set ``PLEXICHAT_JANUS_URL`` (and
   ``PLEXICHAT_JANUS_SECRET`` if the gateway needs one) to a running Janus with
   ``janus.plugin.videoroom`` and a real offer is negotiated: room creation,
   publisher join, a genuine Janus SDP answer, trickled candidate, layer
   configure, and teardown. Without that env var the test skips -- loudly, so a
   green run never implies the media plane was exercised.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

import pytest

from src.core.voice.signaling.exceptions import SFUConnectionError
from src.core.voice.signaling.sfu.janus import JanusAdapter

GATEWAY_URL = os.environ.get("PLEXICHAT_JANUS_URL", "").strip()
GATEWAY_SECRET = os.environ.get("PLEXICHAT_JANUS_SECRET", "").strip()

requires_gateway = pytest.mark.skipif(
    not GATEWAY_URL,
    reason=(
        "no Janus gateway configured; set PLEXICHAT_JANUS_URL (and "
        "PLEXICHAT_JANUS_SECRET if required) to exercise the real media plane"
    ),
)


# ---------------------------------------------------------------------------
# 1. No gateway: fail closed
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_unreachable_gateway_raises_instead_of_faking_a_room() -> None:
    """A closed port must fail the room, never return a usable-looking one."""
    adapter = JanusAdapter(api_url="http://127.0.0.1:9", timeout=2)

    with pytest.raises(SFUConnectionError):
        await adapter.create_room(f"probe-{uuid.uuid4().hex[:8]}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_an_unknown_room_cannot_produce_an_answer() -> None:
    """No session means no answer -- the offer fails closed, not silently."""
    adapter = JanusAdapter(api_url="http://127.0.0.1:9", timeout=2)

    with pytest.raises(SFUConnectionError):
        await adapter.apply_remote_offer("voice_404", "user_7", "v=0\r\n")


# ---------------------------------------------------------------------------
# 2. A real gateway: the media handshake actually completes
# ---------------------------------------------------------------------------


async def _real_offer() -> str:
    """A real SDP offer from aiortc (the browser-equivalent peer)."""
    aiortc = pytest.importorskip("aiortc")
    pc = aiortc.RTCPeerConnection()
    try:
        pc.addTransceiver("audio", direction="sendrecv")
        offer = await pc.createOffer()
        return offer.sdp
    finally:
        await pc.close()


@asynccontextmanager
async def _room_on_gateway():
    """A real Janus room, torn down even when the test body fails."""
    adapter = JanusAdapter(api_url=GATEWAY_URL, timeout=15, api_secret=GATEWAY_SECRET)
    room_id = f"voice_e2e_{uuid.uuid4().hex[:8]}"
    await adapter.create_room(room_id)
    try:
        yield adapter, room_id
    finally:
        try:
            await adapter.close_room(room_id)
        finally:
            await adapter.close()


@pytest.mark.integration
@requires_gateway
@pytest.mark.asyncio
async def test_publish_handshake_against_a_real_gateway() -> None:
    """The offer the client sends is answered by Janus itself."""
    async with _room_on_gateway() as (adapter, room_id):
        await adapter.join_room(room_id, "user_7")
        offer = await _real_offer()
        negotiated = await adapter.apply_remote_offer(room_id, "user_7", offer)

        # Janus's own answer, not ours: it must carry real ICE credentials and
        # an audio m-line that our offer asked for.
        answer = negotiated["sdp"]
        assert "a=ice-ufrag:" in answer
        assert "a=ice-pwd:" in answer
        assert "m=audio" in answer
        assert negotiated["type"] == "answer"


@pytest.mark.integration
@requires_gateway
@pytest.mark.asyncio
async def test_trickled_candidate_reaches_the_gateway() -> None:
    """A candidate the client trickles is relayed to the gateway."""
    async with _room_on_gateway() as (adapter, room_id):
        await adapter.join_room(room_id, "user_7")
        await adapter.apply_remote_offer(room_id, "user_7", await _real_offer())

        accepted = await adapter.add_ice_candidate(
            room_id,
            "user_7",
            "candidate:1 1 udp 2130706431 203.0.113.7 54321 typ host",
            sdp_mid="audio",
            sdp_mline_index=0,
        )
        assert accepted is True


@pytest.mark.integration
@requires_gateway
@pytest.mark.asyncio
async def test_layer_configure_against_a_real_gateway() -> None:
    """A peer with no subscriber handle reports the hint as not applied."""
    async with _room_on_gateway() as (adapter, room_id):
        await adapter.join_room(room_id, "user_7")

        # The peer published; it has no subscriber handle of its own to
        # configure, so the honest answer is "not applied" rather than True.
        assert await adapter.set_preferred_layers(room_id, "user_7", "", 0, 0) is False
