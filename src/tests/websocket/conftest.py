"""
WebSocket test fixtures.
"""

import pytest
from unittest.mock import Mock

# common_utils is now a native package.


# Config is already setup in the main conftest.py at import time
# No need to setup again here

from src.api.websocket.connection import ConnectionState  # noqa: E402


@pytest.fixture
def connection():
    """Mock WebSocket connection for tests."""
    conn = Mock()
    conn.connection_id = "test_conn_123"
    conn.state = ConnectionState.CONNECTING
    conn.user_id = None
    conn.session_id = None
    conn.ip_address = "127.0.0.1"
    conn.user_agent = "TestClient/1.0"
    return conn
