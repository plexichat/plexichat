"""Regression coverage for mediasoup WebSocket recording cleanup."""

from unittest.mock import AsyncMock, Mock

import pytest

from src.core.voice.signaling.sfu.base import SFUConsumer, MediaKind
from src.core.voice.signaling.sfu.mediasoup_ws import (
    MediasoupWSAdapter,
    PeerConnection,
)


@pytest.mark.asyncio
async def test_stop_recording_closes_recorder_consumers_and_transport(tmp_path):
    """Stopping a recording must release its server-side media resources."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._recordings = {
        "room-1": {
            "recording_id": "rec-1",
            "output_dir": str(tmp_path),
            "transport_id": "transport-rec",
            "consumer_ids": ["consumer-a", "consumer-b"],
            "recorder_peer_id": "peer-rec",
            "file_count": 0,
        }
    }

    connection = PeerConnection(
        peer_id="peer-rec",
        room_id="room-1",
        websocket=Mock(),
        recv_transport_id="transport-rec",
        consumers={
            "consumer-a": SFUConsumer(
                id="consumer-a",
                producer_id="producer-a",
                kind=MediaKind.AUDIO,
                rtp_parameters={},
            ),
            "consumer-b": SFUConsumer(
                id="consumer-b",
                producer_id="producer-b",
                kind=MediaKind.AUDIO,
                rtp_parameters={},
            ),
        },
    )
    adapter._connections[adapter._get_connection_key("room-1", "peer-rec")] = connection
    adapter._request = AsyncMock(return_value={})

    result = await adapter.stop_recording("room-1")

    assert result == [str(tmp_path / "rec-1.webm")]
    assert [call.args[1] for call in adapter._request.await_args_list] == [
        "closeConsumer",
        "closeConsumer",
        "closeWebRtcTransport",
    ]
    assert connection.consumers == {}
    assert connection.recv_transport_id is None
    assert "room-1" not in adapter._recordings


@pytest.mark.asyncio
async def test_stop_recording_preserves_state_on_unexpected_exception(
    tmp_path, monkeypatch
):
    """Unexpected cleanup errors must not discard retry metadata."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._recordings = {
        "room-unexpected": {
            "recording_id": "rec-unexpected",
            "output_dir": str(tmp_path),
            "transport_id": "transport-unexpected",
            "consumer_ids": [],
            "recorder_peer_id": "peer-unexpected",
            "file_count": 0,
        }
    }
    connection = PeerConnection(
        peer_id="peer-unexpected",
        room_id="room-unexpected",
        websocket=Mock(),
    )
    adapter._connections["room-unexpected:peer-unexpected"] = connection
    adapter._request = AsyncMock(return_value={})
    monkeypatch.setattr(
        "src.core.voice.signaling.sfu.mediasoup_ws.ensure_within_recording_dir",
        Mock(side_effect=ValueError("unexpected cleanup failure")),
    )

    with pytest.raises(ValueError, match="unexpected cleanup failure"):
        await adapter.stop_recording("room-unexpected")

    assert "room-unexpected" in adapter._recordings
    # Transport teardown succeeded before the later path-processing error;
    # preserving the entry with transport_id cleared is the correct retry state.
    assert adapter._recordings["room-unexpected"]["transport_id"] is None


@pytest.mark.asyncio
async def test_partial_teardown_retains_only_failed_resources(tmp_path):
    """A partial SFU cleanup must retry only resources that actually failed."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._recordings = {
        "room-2": {
            "recording_id": "rec-2",
            "output_dir": str(tmp_path),
            "transport_id": "transport-rec",
            "consumer_ids": ["consumer-failed", "consumer-closed"],
            "recorder_peer_id": "peer-rec",
            "file_count": 0,
        }
    }
    connection = PeerConnection(
        peer_id="peer-rec",
        room_id="room-2",
        websocket=Mock(),
        recv_transport_id="transport-rec",
        consumers={
            "consumer-failed": Mock(),
            "consumer-closed": Mock(),
        },
    )
    adapter._connections[adapter._get_connection_key("room-2", "peer-rec")] = connection

    async def request(_connection, method, data):
        if method == "closeConsumer" and data["consumerId"] == "consumer-failed":
            raise RuntimeError("transient consumer failure")
        return {}

    adapter._request = AsyncMock(side_effect=request)

    await adapter.stop_recording("room-2")

    assert adapter._recordings["room-2"]["consumer_ids"] == ["consumer-failed"]
    assert adapter._recordings["room-2"]["transport_id"] is None
    assert "consumer-failed" in connection.consumers
    assert "consumer-closed" not in connection.consumers
    assert connection.recv_transport_id is None


@pytest.mark.asyncio
async def test_close_records_unrecoverable_teardown_failure(tmp_path):
    """Adapter shutdown moves failed cleanup to explicit diagnostics."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._rooms = {}
    adapter._message_handlers = {}
    adapter._recordings = {
        "room-3": {
            "recording_id": "rec-3",
            "output_dir": str(tmp_path),
            "consumer_ids": ["consumer-3"],
            "transport_id": "transport-3",
            "recorder_peer_id": "peer-3",
            "file_count": 0,
        }
    }
    adapter._recording_teardown_failures = {}
    websocket = AsyncMock()
    adapter._connections["room-3:peer-3"] = PeerConnection(
        peer_id="peer-3",
        room_id="room-3",
        websocket=websocket,
    )

    async def failed_stop(room_id):
        assert room_id == "room-3"
        raise RuntimeError("signaling unavailable")

    adapter.stop_recording = AsyncMock(side_effect=failed_stop)

    await adapter.close()

    assert adapter._recordings == {}
    assert adapter._recording_teardown_failures["room-3"]["reason"] == (
        "adapter closed before recorder teardown completed"
    )
    websocket.close.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_close_retries_unexpected_teardown_before_recording_failure(tmp_path):
    """Shutdown retries preserved state before declaring teardown unrecoverable."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._rooms = {}
    adapter._message_handlers = {}
    adapter._recordings = {
        "room-retry": {
            "recording_id": "rec-retry",
            "output_dir": str(tmp_path),
            "consumer_ids": [],
            "transport_id": None,
            "recorder_peer_id": None,
            "file_count": 0,
        }
    }
    adapter._recording_teardown_failures = {}
    calls = 0

    async def stop_with_retry(room_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient shutdown failure")
        adapter._recordings.pop(room_id, None)
        return []

    adapter.stop_recording = AsyncMock(side_effect=stop_with_retry)

    await adapter.close()

    assert adapter.stop_recording.await_count == 2
    assert adapter._recordings == {}
    assert adapter._recording_teardown_failures == {}


@pytest.mark.asyncio
async def test_close_room_retries_unexpected_teardown_before_recording_failure(
    tmp_path,
):
    """Room shutdown retries preserved state before recording a diagnostic."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._rooms = {"room-room-retry": Mock()}
    adapter._message_handlers = {}
    adapter._recordings = {
        "room-room-retry": {
            "recording_id": "rec-room-retry",
            "output_dir": str(tmp_path),
            "consumer_ids": [],
            "transport_id": None,
            "recorder_peer_id": None,
            "file_count": 0,
        }
    }
    adapter._recording_teardown_failures = {}
    calls = 0

    async def stop_with_retry(room_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient room shutdown failure")
        adapter._recordings.pop(room_id, None)
        return []

    adapter.stop_recording = AsyncMock(side_effect=stop_with_retry)

    assert await adapter.close_room("room-room-retry") is True

    assert adapter.stop_recording.await_count == 2
    assert adapter._recordings == {}
    assert adapter._recording_teardown_failures == {}


@pytest.mark.asyncio
async def test_close_room_records_failure_and_still_closes_connections(tmp_path):
    """Room shutdown records failed cleanup without leaking its socket."""
    adapter = MediasoupWSAdapter.__new__(MediasoupWSAdapter)
    adapter._connections = {}
    adapter._rooms = {"room-4": Mock()}
    adapter._message_handlers = {}
    adapter._recordings = {
        "room-4": {
            "recording_id": "rec-4",
            "output_dir": str(tmp_path),
            "consumer_ids": ["consumer-4"],
            "transport_id": "transport-4",
            "recorder_peer_id": "peer-4",
            "file_count": 0,
        }
    }
    adapter._recording_teardown_failures = {}
    websocket = AsyncMock()
    adapter._connections["room-4:peer-4"] = PeerConnection(
        peer_id="peer-4",
        room_id="room-4",
        websocket=websocket,
    )

    async def failed_stop(_room_id):
        raise RuntimeError("room signaling failed")

    adapter.stop_recording = AsyncMock(side_effect=failed_stop)

    assert await adapter.close_room("room-4") is True

    assert adapter.stop_recording.await_count == 2
    assert adapter._recordings == {}
    assert "room-4" in adapter._recording_teardown_failures
    assert (
        "room signaling failed"
        in adapter._recording_teardown_failures["room-4"]["reason"]
    )
    assert adapter._connections == {}
    assert "room-4" not in adapter._rooms
    websocket.close.assert_awaited_once_with()
