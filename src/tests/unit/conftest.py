"""
Unit test fixtures.
"""

import pytest
import tempfile

# common_utils is now a native package.


import utils.config as config  # noqa: E402
import utils.version as version  # noqa: E402

# Setup config before importing modules
DEFAULT_TEST_CONFIG = {
    "api": {
        "cors_origins": ["http://testserver", "http://localhost:3000"],
    },
}

config.setup(
    config_path=tempfile.mktemp(suffix=".yaml"), default_config=DEFAULT_TEST_CONFIG
)
version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")


@pytest.fixture
def mock_api_server():
    """Mock API server for self-test runner."""
    return "http://localhost:8000"
