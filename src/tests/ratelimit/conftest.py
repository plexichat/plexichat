"""
Rate limit test fixtures.
"""

import pytest
import os
import sys

# Setup paths at import time
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
utils_path = os.path.join(project_root, "src", "utils")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, utils_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

# Config is already setup in the main conftest.py at import time
# No need to setup again here

from src.core.ratelimit.models import RateLimitConfig


@pytest.fixture
def default_config():
    """Default rate limit configuration for tests."""
    return RateLimitConfig(
        requests=100,
        window_seconds=60.0,
        burst=10,
    )
