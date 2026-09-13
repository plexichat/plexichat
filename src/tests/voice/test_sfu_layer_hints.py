"""Layer hints must reach a real consumer on every backend that has simulcast.

``BandwidthEstimator`` calls ``set_preferred_layers(room, peer, "", spatial,
temporal)``: it keys quality data by channel/user and has no consumer mapping,
so it passes an empty consumer id. Two backends could not resolve that:

- ``mediasoup-ws`` sent ``consumerId: ""`` to the SFU, which cannot resolve a
  consumer, so the degradation hint silently never applied.
- ``mediasoup`` (REST) passed the empty id to ``path_segment``, which rejects
  empty values -- the request raised before it was ever sent.

Both now resolve the peer's own consumers (video preferred: layers only mean
something for simulcast video), and report ``False`` when there is nothing to
configure.

aiortc has no simulcast API, but it does expose ``RTCRtpSender.replaceTrack``,
which is the bandwidth lever this backend has: a degraded peer's outbound
video is suspended and re-attached when the hint is lifted. The application of
that lever lives in ``test_sfu_aiortc_layer_hint_apply.py``; here we pin that a
peer with nothing to change still gets an honest ``False``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.voice.signaling.manager.quality_mixin import BandwidthEstimator
from src.core.voice.signaling.sfu.aiortc_adapter import AiortcAdapter
from src.core.voice.signaling.sfu.base import MediaKind, SFUConsumer
from src.core.voice.signaling.sfu.mediasoup import MediasoupAdapter
from src.core.voice.signaling.sfu.mediasoup_ws import MediasoupWSAdapter, PeerConnection


def _consumer(consumer_id: str, kind: MediaKind = MediaKind.VIDEO) -> SFUConsumer:
    return SFUConsumer(
        id=consumer_id, producer_id=f"prod-{consumer_id}", kind=kind, rtp_parameters={}
    )


# === mediasoup (REST) ======================================================


def _rest_adapter(*consumers: SFUConsumer, room: str = "voice_9") -> MediasoupAdapter:
    adapter = MediasoupAdapter(api_url="http://localhost:3000")
    adapter._request = AsyncMock(return_value={})
    if consumers:
        adapter._consumers[(room, "user_5")] = list(consumers)
    return adapter


@pytest.mark.asyncio
async def test_rest_resolves_consumers_for_an_empty_id() -> None:
    adapter = _rest_adapter(_consumer("c-video"), room="voice_9")

    applied = await adapter.set_preferred_layers("voice_9", "user_5", "", 0, 0)

    assert applied is True
    assert adapter._request.await_count == 1
    method, url, payload = adapter._request.await_args.args
    assert method == "POST"
    assert url.endswith("consumers/c-video/preferredLayers")
    assert payload == {"spatialLayer": 0, "temporalLayer": 0}


@pytest.mark.asyncio
async def test_rest_prefers_video_consumers() -> None:
    adapter = _rest_adapter(
        _consumer("c-audio", MediaKind.AUDIO), _consumer("c-video"), room="voice_9"
    )

    await adapter.set_preferred_layers("voice_9", "user_5", "", 2, 2)

    urls = [call.args[1] for call in adapter._request.await_args_list]
    assert urls == ["/rooms/voice_9/peers/user_5/consumers/c-video/preferredLayers"]


@pytest.mark.asyncio
async def test_rest_reports_false_with_no_tracked_consumers() -> None:
    adapter = _rest_adapter(room="voice_9")

    assert await adapter.set_preferred_layers("voice_9", "user_5", "", 0, 0) is False
    adapter._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_rest_still_honours_an_explicit_consumer_id() -> None:
    adapter = _rest_adapter(room="voice_9")

    applied = await adapter.set_preferred_layers("voice_9", "user_5", "explicit", 1, 1)

    assert applied is True
    assert adapter._request.await_args.args[1].endswith(
        "consumers/explicit/preferredLayers"
    )


@pytest.mark.asyncio
async def test_rest_forgets_consumers_when_the_peer_leaves() -> None:
    adapter = _rest_adapter(_consumer("c-video"), room="voice_9")
    adapter._joined_peers["voice_9"] = {"user_5"}
    adapter._request = AsyncMock(return_value={})

    await adapter.leave_room("voice_9", "user_5")

    assert await adapter.set_preferred_layers("voice_9", "user_5", "", 0, 0) is False


# === mediasoup-ws ==========================================================


def _ws_adapter(*consumers: SFUConsumer, room: str = "voice_9") -> MediasoupWSAdapter:
    adapter = MediasoupWSAdapter(ws_url="wss://localhost:4443")
    adapter._request = AsyncMock(return_value={})
    connection = PeerConnection(peer_id="user_5", room_id=room, websocket=None)
    for consumer in consumers:
        connection.consumers[consumer.id] = consumer
    adapter._connections[f"{room}:user_5"] = connection
    return adapter


@pytest.mark.asyncio
async def test_ws_resolves_consumers_for_an_empty_id() -> None:
    adapter = _ws_adapter(_consumer("c-video"), room="voice_9")

    applied = await adapter.set_preferred_layers("voice_9", "user_5", "", 0, 0)

    assert applied is True
    _connection, method, payload = adapter._request.await_args.args
    assert method == "setConsumerPreferredLayers"
    assert payload == {"consumerId": "c-video", "spatialLayer": 0, "temporalLayer": 0}


@pytest.mark.asyncio
async def test_ws_prefers_video_consumers() -> None:
    adapter = _ws_adapter(
        _consumer("c-audio", MediaKind.AUDIO), _consumer("c-video"), room="voice_9"
    )

    await adapter.set_preferred_layers("voice_9", "user_5", "", 2, 2)

    payloads = [call.args[2] for call in adapter._request.await_args_list]
    assert [payload["consumerId"] for payload in payloads] == ["c-video"]


@pytest.mark.asyncio
async def test_ws_reports_false_with_no_consumers() -> None:
    adapter = _ws_adapter(room="voice_9")

    assert await adapter.set_preferred_layers("voice_9", "user_5", "", 0, 0) is False
    adapter._request.assert_not_awaited()


@pytest.mark.asyncio
async def test_ws_reports_false_without_a_connection() -> None:
    adapter = _ws_adapter(_consumer("c-video"), room="voice_9")

    assert await adapter.set_preferred_layers("voice_9", "user_99", "", 0, 0) is False


# === aiortc ================================================================


@pytest.mark.asyncio
async def test_aiortc_reports_the_hint_was_not_applied_without_a_peer() -> None:
    """No peer, no sender changed: the honest answer is False."""
    assert await AiortcAdapter().set_preferred_layers("r", "p", "c", 0, 0) is False


# === the estimator's call shape ===========================================


@pytest.mark.asyncio
async def test_estimator_passes_an_empty_id_to_be_resolved_by_the_adapter() -> None:
    """The contract the adapters above implement depends on this call shape."""
    import asyncio
    import time
    from unittest.mock import MagicMock

    sfu = MagicMock()
    sfu.set_preferred_layers = AsyncMock(return_value=True)
    data = {(123, 42): {"packet_loss": 20.0, "jitter": 5.0, "_ts": time.time()}}
    estimator = BandwidthEstimator(quality_data=data, sfu_adapter=sfu, check_interval=0)

    from unittest.mock import patch

    with patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError()]):
        await estimator._run()

    sfu.set_preferred_layers.assert_awaited_once_with("voice_123", "user_42", "", 0, 0)
