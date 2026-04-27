"""
Voice test fixtures.
"""

import pytest
import os
import sys
import tempfile

# Setup paths at import time
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.config as config
import utils.version as version

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
