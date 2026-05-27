"""
Voice test fixtures.
"""

import pytest
import tempfile

# common-utils is now a native package.


import utils.config as config  # noqa: E402
import utils.version as version  # noqa: E402

# Setup config before importing voice modules
DEFAULT_TEST_CONFIG = {
    "voice": {
        "enabled": True,
        "janus_url": "http://localhost:8088/janus",
    },
}

config.setup(
    config_path=tempfile.mktemp(suffix=".yaml"), default_config=DEFAULT_TEST_CONFIG
)
version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")


@pytest.fixture
def mock_voice_config():
    """Mock voice configuration for tests."""
    return {
        "enabled": True,
        "janus_url": "http://localhost:8088/janus",
        "turn_servers": [],
    }
