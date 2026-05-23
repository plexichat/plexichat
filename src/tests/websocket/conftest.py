"""
WebSocket test fixtures.
"""

import pytest
import os
import sys
from unittest.mock import Mock

# Setup paths at import time
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
# common_utils imported via standard src.utils.common_utils.utils path
for path in [project_root, src_path, utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)


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
