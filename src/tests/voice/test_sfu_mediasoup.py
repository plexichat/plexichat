"""
Tests for mediasoup SFU adapter.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# common_utils imported via standard src.utils.common_utils.utils path
import src.utils.common_utils.utils.logger as logger
# noqa: E402

# Setup logger for tests
try:
    logger.setup(log_dir="temp_test_session/logs", level="WARNING", zip_logs=False)
except Exception:
    pass

from src.core.voice.signaling.sfu.mediasoup import MediasoupAdapter  # noqa: E402
from src.core.voice.signaling.sfu.base import (  # noqa: E402
    TransportDirection,
    MediaKind,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    RoomInfo,
)
from src.core.voice.signaling.exceptions import SFUConnectionError  # noqa: E402


@pytest.fixture
def mock_aiohttp_session():
    """Create a mock aiohttp session."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_type = "application/json"
    mock_response.json = AsyncMock(return_value={})
    mock_response.text = AsyncMock(return_value="")

    mock_session = MagicMock()
    mock_session.request = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response))
    )
    mock_session.close = AsyncMock()

    return mock_session, mock_response


class TestMediasoupAdapter:
    """Tests for MediasoupAdapter."""

    @pytest.mark.asyncio
    async def test_create_room(self, mock_aiohttp_session):
        """Test creating a room."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(
            return_value={
                "peers": [],
                "producers": [],
            }
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            room = await adapter.create_room("room_123")

            assert room is not None
            assert isinstance(room, RoomInfo)
            assert room.id == "room_123"

    @pytest.mark.asyncio
    async def test_close_room(self, mock_aiohttp_session):
        """Test closing a room."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.close_room("room_123")

            assert result is True

    @pytest.mark.asyncio
    async def test_join_room(self, mock_aiohttp_session):
        """Test joining a room."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(
            return_value={
                "routerRtpCapabilities": {"codecs": []},
                "peers": ["peer_1"],
                "producers": [],
            }
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.join_room("room_123", "peer_456")

            assert result is not None
            assert "routerRtpCapabilities" in result
            assert "peers" in result

    @pytest.mark.asyncio
    async def test_leave_room(self, mock_aiohttp_session):
        """Test leaving a room."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.leave_room("room_123", "peer_456")

            assert result is True

    @pytest.mark.asyncio
    async def test_create_transport(self, mock_aiohttp_session):
        """Test creating a transport."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(
            return_value={
                "id": "transport_789",
                "iceParameters": {"usernameFragment": "abc", "password": "xyz"},
                "iceCandidates": [],
                "dtlsParameters": {"fingerprints": []},
            }
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            transport = await adapter.create_transport(
                "room_123", "peer_456", TransportDirection.SEND
            )

            assert transport is not None
            assert isinstance(transport, SFUTransport)
            assert transport.id == "transport_789"
            assert transport.direction == TransportDirection.SEND

    @pytest.mark.asyncio
    async def test_connect_transport(self, mock_aiohttp_session):
        """Test connecting a transport."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.connect_transport(
                "room_123",
                "peer_456",
                "transport_789",
                {"fingerprints": [], "role": "client"},
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_produce(self, mock_aiohttp_session):
        """Test creating a producer."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(
            return_value={
                "id": "producer_abc",
                "paused": False,
            }
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            producer = await adapter.produce(
                "room_123",
                "peer_456",
                "transport_789",
                MediaKind.AUDIO,
                {"codecs": []},
            )

            assert producer is not None
            assert isinstance(producer, SFUProducer)
            assert producer.id == "producer_abc"
            assert producer.kind == MediaKind.AUDIO

    @pytest.mark.asyncio
    async def test_consume(self, mock_aiohttp_session):
        """Test creating a consumer."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(
            return_value={
                "id": "consumer_xyz",
                "kind": "audio",
                "rtpParameters": {},
                "paused": False,
            }
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            consumer = await adapter.consume(
                "room_123",
                "peer_456",
                "transport_789",
                "producer_abc",
                {"codecs": []},
            )

            assert consumer is not None
            assert isinstance(consumer, SFUConsumer)
            assert consumer.id == "consumer_xyz"
            assert consumer.producer_id == "producer_abc"

    @pytest.mark.asyncio
    async def test_pause_producer(self, mock_aiohttp_session):
        """Test pausing a producer."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.pause_producer(
                "room_123", "peer_456", "producer_abc"
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_resume_producer(self, mock_aiohttp_session):
        """Test resuming a producer."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.resume_producer(
                "room_123", "peer_456", "producer_abc"
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_close_producer(self, mock_aiohttp_session):
        """Test closing a producer."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.close_producer(
                "room_123", "peer_456", "producer_abc"
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_get_room_info(self, mock_aiohttp_session):
        """Test getting room info."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(
            return_value={
                "peers": ["peer_1", "peer_2"],
                "producers": ["producer_1"],
            }
        )

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            info = await adapter.get_room_info("room_123")

            assert info is not None
            assert info.id == "room_123"
            assert len(info.peers) == 2

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_aiohttp_session):
        """Test health check success."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_aiohttp_session):
        """Test that API errors raise SFUConnectionError."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.status = 500
        mock_response.text = AsyncMock(return_value="Internal Server Error")

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            with pytest.raises(SFUConnectionError):
                await adapter.create_room("room_123")

    @pytest.mark.asyncio
    async def test_set_preferred_layers(self, mock_aiohttp_session):
        """Test setting preferred simulcast layers."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            result = await adapter.set_preferred_layers(
                "room_123",
                "peer_456",
                "consumer_xyz",
                spatial_layer=2,
                temporal_layer=2,
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_close_adapter(self, mock_aiohttp_session):
        """Test closing the adapter."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = MediasoupAdapter(api_url="http://localhost:3000")
            adapter._session = mock_session

            await adapter.close()

            mock_session.close.assert_called_once()
            assert adapter._session is None
