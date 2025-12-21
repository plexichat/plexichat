"""
Tests for Janus SFU adapter.
"""

import pytest
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Setup paths
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")
if common_utils_path not in sys.path:
    sys.path.insert(0, common_utils_path)

import utils.logger as logger

# Setup logger for tests
try:
    logger.setup(log_dir="temp_test_session/logs", level="WARNING", zip_logs=False)
except Exception:
    pass

from src.core.voice.signaling.sfu.janus import JanusAdapter
from src.core.voice.signaling.sfu.base import (
    TransportDirection,
    MediaKind,
    SFUTransport,
    SFUProducer,
    SFUConsumer,
    RoomInfo,
)
from src.core.voice.signaling.exceptions import SFUConnectionError


@pytest.fixture
def mock_aiohttp_session():
    """Create a mock aiohttp session."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"janus": "success"})

    mock_context = AsyncMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_response)
    mock_context.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_context)
    mock_session.close = AsyncMock()

    return mock_session, mock_response


class TestJanusAdapter:
    """Tests for JanusAdapter."""

    @pytest.mark.asyncio
    async def test_create_room(self, mock_aiohttp_session):
        """Test creating a room."""
        mock_session, mock_response = mock_aiohttp_session

        # Mock session creation
        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},  # create session
            {"janus": "success", "data": {"id": 67890}},  # attach plugin
            {"janus": "success", "plugindata": {"data": {"videoroom": "created"}}},  # create room
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            room = await adapter.create_room("room_123")

            assert room is not None
            assert isinstance(room, RoomInfo)
            assert room.id == "room_123"

    @pytest.mark.asyncio
    async def test_close_room(self, mock_aiohttp_session):
        """Test closing a room."""
        mock_session, mock_response = mock_aiohttp_session

        # Setup existing session
        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success"},  # destroy room
            {"janus": "success"},  # destroy session
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            # First create a room
            await adapter.create_room("room_123")

            # Then close it
            result = await adapter.close_room("room_123")

            assert result is True

    @pytest.mark.asyncio
    async def test_join_room(self, mock_aiohttp_session):
        """Test joining a room."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},  # create session
            {"janus": "success", "data": {"id": 67890}},  # attach plugin (admin)
            {"janus": "success"},  # create room
            {"janus": "success", "data": {"id": 11111}},  # attach plugin (peer)
            {"janus": "success", "plugindata": {"data": {"publishers": []}}},  # join
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")
            result = await adapter.join_room("room_123", "peer_456")

            assert result is not None
            assert "peers" in result

    @pytest.mark.asyncio
    async def test_leave_room(self, mock_aiohttp_session):
        """Test leaving a room."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success", "data": {"id": 11111}},
            {"janus": "success", "plugindata": {"data": {"publishers": []}}},
            {"janus": "success"},  # leave
            {"janus": "success"},  # detach
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")
            await adapter.join_room("room_123", "peer_456")
            result = await adapter.leave_room("room_123", "peer_456")

            assert result is True

    @pytest.mark.asyncio
    async def test_create_transport(self, mock_aiohttp_session):
        """Test creating a transport."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            transport = await adapter.create_transport(
                "room_123", "peer_456", TransportDirection.SEND
            )

            assert transport is not None
            assert isinstance(transport, SFUTransport)
            assert transport.direction == TransportDirection.SEND

    @pytest.mark.asyncio
    async def test_produce(self, mock_aiohttp_session):
        """Test creating a producer."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success", "data": {"id": 11111}},
            {"janus": "success", "plugindata": {"data": {"publishers": []}}},
            {"janus": "success"},  # configure
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")
            await adapter.join_room("room_123", "peer_456")

            producer = await adapter.produce(
                "room_123",
                "peer_456",
                "transport_789",
                MediaKind.AUDIO,
                {"codecs": []},
            )

            assert producer is not None
            assert isinstance(producer, SFUProducer)
            assert producer.kind == MediaKind.AUDIO

    @pytest.mark.asyncio
    async def test_consume(self, mock_aiohttp_session):
        """Test creating a consumer."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success", "data": {"id": 22222}},  # attach for subscriber
            {"janus": "success", "plugindata": {"data": {}}},  # join as subscriber
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")

            consumer = await adapter.consume(
                "room_123",
                "peer_456",
                "transport_789",
                "peer_other_audio",
                {"codecs": []},
            )

            assert consumer is not None
            assert isinstance(consumer, SFUConsumer)

    @pytest.mark.asyncio
    async def test_pause_producer(self, mock_aiohttp_session):
        """Test pausing a producer."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success", "data": {"id": 11111}},
            {"janus": "success", "plugindata": {"data": {"publishers": []}}},
            {"janus": "success"},  # configure (pause)
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")
            await adapter.join_room("room_123", "peer_456")

            result = await adapter.pause_producer("room_123", "peer_456", "producer_audio")

            assert result is True

    @pytest.mark.asyncio
    async def test_resume_producer(self, mock_aiohttp_session):
        """Test resuming a producer."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success", "data": {"id": 11111}},
            {"janus": "success", "plugindata": {"data": {"publishers": []}}},
            {"janus": "success"},  # configure (resume)
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")
            await adapter.join_room("room_123", "peer_456")

            result = await adapter.resume_producer("room_123", "peer_456", "producer_audio")

            assert result is True

    @pytest.mark.asyncio
    async def test_get_room_info(self, mock_aiohttp_session):
        """Test getting room info."""
        mock_session, mock_response = mock_aiohttp_session

        mock_response.json = AsyncMock(side_effect=[
            {"janus": "success", "data": {"id": 12345}},
            {"janus": "success", "data": {"id": 67890}},
            {"janus": "success"},
            {"janus": "success", "plugindata": {"data": {"participants": [
                {"id": 1, "display": "peer_1"},
                {"id": 2, "display": "peer_2"},
            ]}}},
        ])

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.create_room("room_123")
            info = await adapter.get_room_info("room_123")

            assert info is not None
            assert info.id == "room_123"
            assert len(info.peers) == 2

    @pytest.mark.asyncio
    async def test_health_check_success(self, mock_aiohttp_session):
        """Test health check success."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(return_value={"janus": "server_info"})

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            result = await adapter.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_failure(self, mock_aiohttp_session):
        """Test health check failure."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(side_effect=Exception("Connection failed"))

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            result = await adapter.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_janus_error_raises(self, mock_aiohttp_session):
        """Test that Janus errors raise SFUConnectionError."""
        mock_session, mock_response = mock_aiohttp_session
        mock_response.json = AsyncMock(return_value={
            "janus": "error",
            "error": {"code": 123, "reason": "Test error"},
        })

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            with pytest.raises(SFUConnectionError):
                await adapter.create_room("room_123")

    @pytest.mark.asyncio
    async def test_get_router_capabilities(self, mock_aiohttp_session):
        """Test getting router capabilities."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            caps = await adapter.get_router_capabilities("room_123")

            assert caps is not None
            assert "codecs" in caps

    @pytest.mark.asyncio
    async def test_close_adapter(self, mock_aiohttp_session):
        """Test closing the adapter."""
        mock_session, mock_response = mock_aiohttp_session

        with patch("aiohttp.ClientSession", return_value=mock_session):
            adapter = JanusAdapter(api_url="http://localhost:8088/janus")
            adapter._session = mock_session

            await adapter.close()

            mock_session.close.assert_called_once()
            assert adapter._session is None
