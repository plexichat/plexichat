"""Tests for the shared SFU quality bridge and pull-based adapters.

``mediasoup_ws`` gets scores pushed by the SFU. The REST adapters
(``MediasoupAdapter``, ``JanusAdapter``) do not, so they poll for stats and
report them through the shared ``report_quality`` sink. These tests cover the
shared helpers, the poller, and both adapters' collection paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.core.voice.signaling.sfu.janus import JanusAdapter
from src.core.voice.signaling.sfu.mediasoup import MediasoupAdapter
from src.core.voice.signaling.sfu.quality import (
    QualityReporter,
    parse_quality_metrics,
)


# === Metric parsing ========================================================


def test_parse_quality_metrics_maps_mediasoup_field_names():
    assert parse_quality_metrics(
        {"bitrate": 90000, "packetLoss": 1.5, "jitter": 12, "roundTripTime": 42}
    ) == {
        "bitrate": 90000,
        "packet_loss": 1.5,
        "jitter": 12,
        "round_trip_time": 42,
    }


def test_parse_quality_metrics_merges_records_last_wins():
    records = [
        {"kind": "transport", "bitrate": 1000},
        {"kind": "producer", "bitrate": 2000, "score": 7},
    ]
    assert parse_quality_metrics(records) == {"bitrate": 2000, "score": 7}


def test_parse_quality_metrics_drops_unknown_and_bad_values():
    assert (
        parse_quality_metrics(
            {"display": "user_1", "id": 5, "jitter": "abc", "score": None}
        )
        == {}
    )
    assert parse_quality_metrics("not-a-record") == {}
    assert parse_quality_metrics([None, {"packet_loss": float("nan")}]) == {}


def test_parse_quality_metrics_keeps_integral_floats_as_int():
    parsed = parse_quality_metrics({"bitrate": "128000.0", "jitter": 2.5})
    assert parsed == {"bitrate": 128000, "jitter": 2.5}


# === Reporter ==============================================================


@pytest.mark.asyncio
async def test_reporter_forwards_samples():
    adapter = Mock()
    adapter.collect_quality = AsyncMock(
        return_value=[("voice_1", "user_2", {"bitrate": 90000})]
    )
    reporter = QualityReporter(adapter)

    await reporter.poll_once()

    adapter.report_quality.assert_called_once_with(
        "voice_1", "user_2", {"bitrate": 90000}
    )


@pytest.mark.asyncio
async def test_reporter_skips_empty_metrics_and_bad_samples():
    adapter = Mock()
    adapter.collect_quality = AsyncMock(
        return_value=[("voice_1", "user_2", {}), ("only-two",), None]
    )
    reporter = QualityReporter(adapter)

    await reporter.poll_once()

    adapter.report_quality.assert_not_called()


@pytest.mark.asyncio
async def test_reporter_contains_collection_failure():
    adapter = Mock()
    adapter.collect_quality = AsyncMock(side_effect=RuntimeError("sfu down"))
    reporter = QualityReporter(adapter)

    # Must not raise.
    await reporter.poll_once()


def test_reporter_start_without_running_loop_defers():
    adapter = Mock()
    reporter = QualityReporter(adapter)
    assert reporter.start() is False
    assert reporter.running is False


@pytest.mark.asyncio
async def test_reporter_start_stop_lifecycle():
    adapter = Mock()
    adapter.collect_quality = AsyncMock(return_value=[])
    reporter = QualityReporter(adapter, interval=1.0)

    assert reporter.start() is True
    assert reporter.running is True
    reporter.stop()
    assert reporter.running is False


# === Shared sink on the base adapter =======================================


def test_report_quality_forwards_canonical_ids():
    adapter = MediasoupAdapter(api_url="http://sfu")
    sink = Mock()
    adapter.set_quality_mixin(sink)

    adapter.report_quality("voice_55", "user_7", {"bitrate": 64000})

    sink.update_quality_hint.assert_called_once_with(55, 7, bitrate=64000)


def test_report_quality_drops_unmappable_ids():
    adapter = MediasoupAdapter(api_url="http://sfu")
    sink = Mock()
    adapter.set_quality_mixin(sink)

    adapter.report_quality("room-1", "peer-1", {"bitrate": 64000})
    adapter.report_quality("voice_55", "user_7", {})

    sink.update_quality_hint.assert_not_called()


def test_report_quality_contains_sink_failure():
    adapter = MediasoupAdapter(api_url="http://sfu")
    sink = Mock()
    sink.update_quality_hint.side_effect = ValueError("boom")
    adapter.set_quality_mixin(sink)

    # Must not raise.
    adapter.report_quality("voice_55", "user_7", {"bitrate": 64000})


# === Mediasoup REST adapter ================================================


@pytest.mark.asyncio
async def test_mediasoup_tracks_joined_peers():
    adapter = MediasoupAdapter(api_url="http://sfu")
    adapter._request = AsyncMock(return_value={})

    await adapter.join_room("voice_1", "user_2")
    assert adapter._joined_peers == {"voice_1": {"user_2"}}

    await adapter.leave_room("voice_1", "user_2")
    assert adapter._joined_peers == {}


@pytest.mark.asyncio
async def test_mediasoup_collect_quality_parses_stats():
    adapter = MediasoupAdapter(api_url="http://sfu")
    adapter._joined_peers = {"voice_1": {"user_2"}}
    adapter._request = AsyncMock(
        return_value={
            "stats": [
                {"type": "transport", "bitrate": 80000},
                {"type": "producer", "packetLoss": 3.0, "jitter": 8.0},
            ]
        }
    )

    samples = await adapter.collect_quality()

    assert samples == [
        ("voice_1", "user_2", {"bitrate": 80000, "packet_loss": 3.0, "jitter": 8.0})
    ]
    assert "/rooms/voice_1/peers/user_2/stats" in adapter._request.await_args.args[1]


@pytest.mark.asyncio
async def test_mediasoup_collect_quality_skips_failing_peer():
    adapter = MediasoupAdapter(api_url="http://sfu")
    adapter._joined_peers = {"voice_1": {"user_2", "user_3"}}
    adapter._request = AsyncMock(side_effect=[RuntimeError("no route"), {"score": 4}])

    samples = await adapter.collect_quality()

    assert len(samples) == 1
    assert samples[0][1] in {"user_2", "user_3"}


@pytest.mark.asyncio
async def test_mediasoup_collect_quality_ignores_metricless_stats():
    adapter = MediasoupAdapter(api_url="http://sfu")
    adapter._joined_peers = {"voice_1": {"user_2"}}
    adapter._request = AsyncMock(return_value={"display": "user_2"})

    assert await adapter.collect_quality() == []


@pytest.mark.asyncio
async def test_mediasoup_set_quality_mixin_starts_and_stops_reporter():
    adapter = MediasoupAdapter(api_url="http://sfu")
    adapter._request = AsyncMock(return_value={})
    adapter.set_quality_mixin(Mock())

    assert adapter._quality_reporter is not None
    assert adapter._quality_reporter.running is True

    with patch.object(adapter, "_request", AsyncMock(return_value={})):
        await adapter.close()
    assert adapter._quality_reporter is None


# === Janus adapter =========================================================


@pytest.mark.asyncio
async def test_janus_collect_quality_reports_metric_bearing_participants():
    adapter = JanusAdapter(api_url="http://janus")
    adapter._janus_sessions = {"voice_9": 111}
    adapter._janus_handles = {"voice_9": {"admin": 222}}
    adapter._send_message = AsyncMock(
        return_value={
            "plugindata": {
                "data": {
                    "participants": [
                        {"id": 1, "display": "user_2", "bitrate": 70000},
                        {"id": 2, "display": "user_3"},
                    ]
                }
            }
        }
    )

    samples = await adapter.collect_quality()

    assert samples == [("voice_9", "user_2", {"bitrate": 70000})]
    body = adapter._send_message.await_args.args[2]
    assert body["request"] == "listparticipants"


@pytest.mark.asyncio
async def test_janus_collect_quality_skips_room_without_admin_handle():
    adapter = JanusAdapter(api_url="http://janus")
    adapter._janus_sessions = {"voice_9": 111}
    adapter._janus_handles = {"voice_9": {"user_2": 333}}
    adapter._send_message = AsyncMock()

    assert await adapter.collect_quality() == []
    adapter._send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_janus_collect_quality_contains_request_failure():
    adapter = JanusAdapter(api_url="http://janus")
    adapter._janus_sessions = {"voice_9": 111}
    adapter._janus_handles = {"voice_9": {"admin": 222}}
    adapter._send_message = AsyncMock(side_effect=RuntimeError("janus down"))

    assert await adapter.collect_quality() == []


@pytest.mark.asyncio
async def test_janus_set_quality_mixin_starts_and_stops_reporter():
    adapter = JanusAdapter(api_url="http://janus")
    adapter.set_quality_mixin(Mock())

    assert adapter._quality_reporter is not None
    assert adapter._quality_reporter.running is True

    adapter._janus_sessions = {}
    adapter._janus_handles = {}
    adapter._janus_recordings = {}
    adapter._janus_recording_roots = {}
    adapter._producer_kinds = {}
    adapter._producer_peers = {}
    adapter._session = None
    await adapter.close()
    assert adapter._quality_reporter is None


# === End-to-end through the reporter =======================================


@pytest.mark.asyncio
async def test_reporter_drives_adapter_sink_end_to_end():
    """The poller must land samples in the quality store with canonical ids."""
    adapter = MediasoupAdapter(api_url="http://sfu")
    adapter._joined_peers = {"voice_42": {"user_8"}}
    adapter._request = AsyncMock(return_value={"bitrate": 96000, "jitter": 5})
    sink = Mock()
    adapter._quality_mixin = sink

    reporter = QualityReporter(adapter)
    await reporter.poll_once()

    sink.update_quality_hint.assert_called_once_with(42, 8, bitrate=96000, jitter=5)
