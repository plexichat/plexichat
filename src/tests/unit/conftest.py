"""
Unit test fixtures.
"""

import pytest

# common_utils is now a native package.


import utils.config as config  # noqa: E402
import utils.version as version  # noqa: E402

# Setup config before importing modules
DEFAULT_TEST_CONFIG = {
    "api": {
        "cors_origins": ["http://testserver", "http://localhost:3000"],
    },
}

# Merge into the root test config instead of re-initializing it:
# config.setup() replaces the entire config, clobbering the
# authentication/search/media sections other suites depend on
# (lockout thresholds, discovery limits, compression defaults).
for _key, _value in DEFAULT_TEST_CONFIG.items():
    _existing = config.get(_key)
    if isinstance(_existing, dict) and isinstance(_value, dict):
        _merged = dict(_existing)
        _merged.update(_value)
        _value = _merged
    config.set(_key, _value)
version.setup(current_version="r.1.0-1", min_supported_version="a.1.0-1")


@pytest.fixture
def mock_api_server():
    """Mock API server for self-test runner."""
    return "http://localhost:8000"
