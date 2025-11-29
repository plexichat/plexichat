"""
Unit test fixtures.

Unit tests should be fast and not require database access.
"""

import pytest


@pytest.fixture
def test_config():
    """Get test configuration without database setup."""
    from src.tests.fixtures.config import get_test_config
    return get_test_config()
