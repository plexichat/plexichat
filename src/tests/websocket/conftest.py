"""Fixtures for WebSocket gateway tests."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from dataclasses import dataclass

import utils.logger as logger
from src.api.websocket.connection import Connection
from src.api.websocket.session import SessionManager
from src.api.websocket.dispatcher import GatewayDispatcher
from src.api.websocket.handlers import OpcodeHandler
from src.core.events.types import GatewayIntent
from src.core import events


@pytest.fixture(autouse=True)
def setup_modules():
    """Setup required modules for tests."""
    if not logger._setup_called:
        logger.setup(log_dir="logs", level="WARNING")
    if not events.is_setup():
        events.setup()
    yield


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_bytes = AsyncMock()
    ws.receive = AsyncMock()
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    return ws


@pytest.fixture
def session_manager():
    """Create a session manager instance."""
    return SessionManager(
        heartbeat_interval_ms=45000,
        session_timeout_ms=60000,
        max_connections_per_user=5,
    )


@pytest.fixture
def connection(mock_websocket, session_manager):
    """Create a connection instance."""
    conn_id = session_manager.generate_connection_id()
    return Connection(
        websocket=mock_websocket,
        connection_id=conn_id,
        heartbeat_interval_ms=session_manager.heartbeat_interval_ms,
    )


@pytest.fixture
def authenticated_connection(mock_websocket, session_manager):
    """Create an authenticated connection."""
    conn_id = session_manager.generate_connection_id()
    conn = Connection(
        websocket=mock_websocket,
        connection_id=conn_id,
        heartbeat_interval_ms=session_manager.heartbeat_interval_ms,
    )
    session_manager.add_connection(conn)
    session_manager.create_session(conn, user_id=12345, intents=GatewayIntent.default_intents())
    return conn


@pytest.fixture
def mock_auth_module():
    """Create a mock auth module."""
    auth = MagicMock()

    @dataclass
    class TokenInfo:
        user_id: int
        permissions: dict

    @dataclass
    class User:
        id: int
        username: str

    auth.verify_token = MagicMock(return_value=TokenInfo(user_id=12345, permissions={}))
    auth.get_user = MagicMock(return_value=User(id=12345, username="testuser"))
    return auth


@pytest.fixture
def mock_events_module():
    """Create a mock events module."""
    events = MagicMock()
    events.subscribe = MagicMock()
    events.unsubscribe = MagicMock()
    return events


@pytest.fixture
def mock_presence_module():
    """Create a mock presence module."""
    presence = MagicMock()
    presence.set_status = MagicMock()
    presence.set_activity = MagicMock()
    return presence


@pytest.fixture
def mock_servers_module():
    """Create a mock servers module."""
    servers = MagicMock()
    servers.get_servers = MagicMock(return_value=[])
    return servers


@pytest.fixture
def opcode_handler(session_manager, mock_auth_module, mock_presence_module, mock_servers_module):
    """Create an opcode handler instance."""
    return OpcodeHandler(
        session_manager=session_manager,
        auth_module=mock_auth_module,
        presence_module=mock_presence_module,
        servers_module=mock_servers_module,
    )


@pytest.fixture
def dispatcher(session_manager, mock_events_module):
    """Create a gateway dispatcher instance."""
    return GatewayDispatcher(
        session_manager=session_manager,
        events_module=mock_events_module,
        rate_limit_per_minute=120,
    )


@pytest.fixture
def sample_identify_payload():
    """Create a sample IDENTIFY payload."""
    return {
        "token": "test_token_12345",
        "intents": GatewayIntent.default_intents(),
        "properties": {
            "os": "windows",
            "browser": "test",
            "device": "test",
        },
    }


@pytest.fixture
def sample_resume_payload():
    """Create a sample RESUME payload."""
    return {
        "token": "test_token_12345",
        "session_id": "test_session_id",
        "seq": 42,
    }


@pytest.fixture
def sample_heartbeat_payload():
    """Create a sample HEARTBEAT payload."""
    return 42


@pytest.fixture
def sample_presence_update_payload():
    """Create a sample PRESENCE_UPDATE payload."""
    return {
        "status": "online",
        "activities": [{"type": 0, "name": "Testing"}],
        "afk": False,
        "since": None,
    }
