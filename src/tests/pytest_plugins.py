"""
Simplified pytest plugins for test execution.

This module provides minimal pytest hooks for test organization.
"""


def pytest_configure(config):
    """Configure pytest with custom markers."""
    # Register custom markers
    config.addinivalue_line(
        "markers", "security: Security-critical test that must not fail"
    )
