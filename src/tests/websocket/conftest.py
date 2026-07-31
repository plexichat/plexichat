"""
WebSocket test fixtures.
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

# common_utils is now a native package.


# Config is already setup in the main conftest.py at import time
# No need to setup again here

from src.api.websocket.connection import Connection, ConnectionState  # noqa: E402
from src.api.websocket.dispatcher import GatewayDispatcher  # noqa: E402
from src.api.websocket.handlers import OpcodeHandler  # noqa: E402
from src.api.websocket.session import SessionManager  # noqa: E402
from src.core.auth.models import AccountType, TokenInfo  # noqa: E402


@pytest.fixture
def mock_websocket():
    """Async transport double with the attributes used by ConnectionHandler."""
    websocket = MagicMock()
    websocket.client = SimpleNamespace(host="127.0.0.1")
    websocket.headers = {"User-Agent": "TestClient/1.0"}
    websocket.send_json = AsyncMock()
    websocket.send_bytes = AsyncMock()
    websocket.close = AsyncMock()
    return websocket


@pytest.fixture
def session_manager():
    """Isolated gateway session manager for websocket tests."""
    return SessionManager(
        heartbeat_interval_ms=45000,
        session_timeout_ms=60000,
        max_connections_per_user=5,
    )


@pytest.fixture
def mock_auth_module():
    """Authentication double returning a complete production TokenInfo."""
    auth = Mock()
    auth.verify_token.return_value = TokenInfo(
        valid=True,
        token_type="user",
        account_id=12345,
        user_id=12345,
        session_id="auth-session",
        permissions={},
        rate_limit_tier="free",
        expires_at=None,
        username="test-user",
        account_type=AccountType.USER,
    )
    auth.get_user.return_value = None
    return auth


@pytest.fixture
def opcode_handler(session_manager, mock_auth_module):
    """Opcode handler wired with the isolated session and auth doubles."""
    return OpcodeHandler(
        session_manager=session_manager,
        auth_module=mock_auth_module,
    )


@pytest.fixture
def dispatcher(session_manager):
    """Gateway dispatcher backed by the test session manager."""
    return GatewayDispatcher(session_manager=session_manager)


@pytest.fixture
def connection(mock_websocket):
    """Concrete unauthenticated gateway connection for handler tests."""
    return Connection(
        websocket=mock_websocket,
        connection_id="test_conn_123",
        heartbeat_interval_ms=45000,
    )


@pytest.fixture
def authenticated_connection(connection):
    """Concrete READY connection for authenticated opcode tests."""
    connection.set_identified(12345, "test-session", 513)
    return connection


@pytest.fixture
def sample_identify_payload():
    """Valid IDENTIFY payload shared by gateway tests."""
    return {
        "token": "test_token",
        "intents": 513,
        "properties": {
            "os": "linux",
            "browser": "pytest",
            "device": "pytest",
        },
    }
