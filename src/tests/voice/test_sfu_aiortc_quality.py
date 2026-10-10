"""aiortc quality reporting: stats mapping and the adapter's poll loop.

Auditing the aiortc adapter against the other SFU backends found it was the
only one with no ``collect_quality``: BandwidthEstimator had no input at all on
the **default** in-process backend, the same gap the janus and mediasoup-REST
adapters had. Quality is now pulled from ``RTCPeerConnection.getStats()``.

The metric mapping is exercised with fake stat records rather than a live media
path, because the interesting part is the unit conversion (aiortc reports
jitter in codec clock-rate units, not milliseconds) and the source preference
(the remote endpoint's report is the downlink signal).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("aiortc")

from src.core.voice.signaling.sfu import aiortc_adapter as mod
from src.core.voice.signaling.sfu.aiortc_adapter import (
    AiortcAdapter,
    _quality_metrics_from_stats,
    _total_bytes_sent,
)


def _inbound(kind: str, lost=None, received=None, jitter=None, type_="inbound-rtp"):
    return SimpleNamespace(
        type=type_,
        kind=kind,
        packetsLost=lost,
        packetsReceived=received,
        jitter=jitter,
    )


def _remote(kind: str, lost=None, received=None, jitter=None, rtt=None):
    return SimpleNamespace(
        type="remote-inbound-rtp",
        kind=kind,
        packetsLost=lost,
        packetsReceived=received,
        jitter=jitter,
        roundTripTime=rtt,
    )


# === Metric mapping =========================================================


def test_remote_report_is_preferred_over_inbound():
    """The remote's report is the downlink signal the estimator needs."""
    metrics = _quality_metrics_from_stats(
        [
            _inbound("audio", lost=0, received=1000, jitter=0),
            _remote("audio", lost=50, received=950, jitter=0, rtt=0.02),
        ]
    )
    # 50 / (950 + 50) = 5%
    assert metrics["packet_loss"] == pytest.approx(5.0)
    assert metrics["round_trip_time"] == pytest.approx(20.0)


def test_falls_back_to_inbound_while_no_remote_report_exists():
    metrics = _quality_metrics_from_stats(
        [_inbound("audio", lost=25, received=75, jitter=480)]
    )
    assert metrics["packet_loss"] == pytest.approx(25.0)
    # 480 / 48000 clock * 1000 = 10 ms
    assert metrics["jitter"] == pytest.approx(10.0)


def test_jitter_is_converted_using_the_codec_clock_rate():
    """aiortc jitter is in clock-rate units, not milliseconds."""
    audio = _quality_metrics_from_stats(
        [_inbound("audio", received=100, jitter=9600)]  # 48000 Hz
    )
    video = _quality_metrics_from_stats(
        [_inbound("video", received=100, jitter=9000)]  # 90000 Hz
    )
    assert audio["jitter"] == pytest.approx(200.0)
    assert video["jitter"] == pytest.approx(100.0)


def test_worst_stream_wins_across_audio_and_video():
    metrics = _quality_metrics_from_stats(
        [
            _remote("audio", lost=0, received=1000, jitter=0),
            _remote("video", lost=0, received=1000, jitter=4500),
        ]
    )
    # Video is worst: 4500 / 90000 * 1000 = 50 ms.
    assert metrics["jitter"] == pytest.approx(50.0)


def test_skips_records_with_nothing_measurable():
    assert _quality_metrics_from_stats([]) == {}
    assert _quality_metrics_from_stats([SimpleNamespace(type="transport")]) == {}
    # Present but non-numeric: dropped rather than guessed at.
    assert (
        _quality_metrics_from_stats(
            [_inbound("audio", lost="nope", received=None, jitter=None)]
        )
        == {}
    )


def test_total_bytes_sent_sums_outbound_streams_only():
    records = [
        SimpleNamespace(type="outbound-rtp", bytesSent=100),
        SimpleNamespace(type="outbound-rtp", bytesSent=250),
        SimpleNamespace(type="inbound-rtp", bytesSent=999),
        SimpleNamespace(type="transport", bytesSent=999),
    ]
    assert _total_bytes_sent(records) == 350
    assert _total_bytes_sent([SimpleNamespace(type="transport")]) is None


# === Adapter collection =====================================================


class _FakePC:
    def __init__(self, records):
        self._records = records

    async def getStats(self):
        if isinstance(self._records, Exception):
            raise self._records
        return self._records


def _adapter_with_peer(records):
    adapter = AiortcAdapter()
    pc = _FakePC(records)
    adapter._rooms["voice_7"] = {"user_3": SimpleNamespace(pc=pc)}
    return adapter, pc


@pytest.mark.asyncio
async def test_collect_quality_reports_measured_metrics():
    adapter, _ = _adapter_with_peer(
        [
            _remote("audio", lost=10, received=90, jitter=960, rtt=0.05),
            SimpleNamespace(type="outbound-rtp", bytesSent=1000),
        ]
    )

    samples = await adapter.collect_quality()

    assert len(samples) == 1
    room_id, peer_id, metrics = samples[0]
    assert (room_id, peer_id) == ("voice_7", "user_3")
    assert metrics["packet_loss"] == pytest.approx(10.0)
    assert metrics["jitter"] == pytest.approx(20.0)
    assert metrics["round_trip_time"] == pytest.approx(50.0)
    # First observation: no rate can be computed yet, so none is claimed.
    assert "bitrate" not in metrics


@pytest.mark.asyncio
async def test_collect_quality_computes_bitrate_from_the_byte_delta(monkeypatch):
    adapter, pc = _adapter_with_peer([])
    # Patch the adapter's module-level ``time`` reference only: mutating the
    # real module's ``monotonic`` would affect the whole process.
    clock = SimpleNamespace(now=100.0)
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: clock.now))

    pc._records = [
        _remote("audio", lost=0, received=100),
        SimpleNamespace(type="outbound-rtp", bytesSent=0),
    ]
    first = await adapter.collect_quality()
    assert first and "bitrate" not in first[0][2]

    # 1000 bytes over 2 s = 4000 bits/s.
    clock.now = 102.0
    pc._records = [
        _remote("audio", lost=0, received=100),
        SimpleNamespace(type="outbound-rtp", bytesSent=1000),
    ]
    second = await adapter.collect_quality()
    assert second[0][2]["bitrate"] == 4000


@pytest.mark.asyncio
async def test_collect_quality_survives_a_failing_get_stats():
    adapter, _ = _adapter_with_peer(RuntimeError("pc is gone"))
    assert await adapter.collect_quality() == []


@pytest.mark.asyncio
async def test_reported_samples_reach_the_canonical_store():
    """End to end: collected SFU ids are mapped back to numeric ids."""
    adapter, pc = _adapter_with_peer([])
    pc._records = [_remote("audio", lost=0, received=100, jitter=960)]

    class Sink:
        def __init__(self):
            self.calls = []

        def update_quality_hint(self, channel_id, user_id, **metrics):
            self.calls.append((channel_id, user_id, metrics))

    sink = Sink()
    adapter.set_quality_mixin(sink)
    samples = await adapter.collect_quality()
    for room_id, peer_id, metrics in samples:
        adapter.report_quality(room_id, peer_id, metrics)

    assert len(sink.calls) == 1
    channel_id, user_id, metrics = sink.calls[0]
    assert (channel_id, user_id) == (7, 3)
    assert metrics["jitter"] == pytest.approx(20.0)
    adapter._quality_reporter.stop()


@pytest.mark.asyncio
async def test_set_quality_mixin_starts_a_reporter_and_close_stops_it():
    adapter = AiortcAdapter()
    adapter.set_quality_mixin(SimpleNamespace())
    assert adapter._quality_reporter is not None
    assert adapter._quality_reporter.running is True

    await adapter.close()
    assert adapter._quality_reporter is None
