"""
Unit test fixtures.
"""

import pytest
import os
import sys
import tempfile

# Setup paths at import time
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
# common_utils imported via standard src.utils.common_utils.utils path
for path in [project_root, src_path, utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)


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
