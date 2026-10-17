"""
Test configuration constants and utilities.

Provides shared test configuration values and constants
used across the test suite.
"""

# Test password for all test users
TEST_PASSWORD = "TestPass123!"


def get_test_config():
    """
    Get test configuration values.

    Returns a dictionary with test configuration values
    that match the expected test environment settings.
    """
    return {
        "authentication": {
            "accounts": {
                "username_min_length": 3,
                "username_max_length": 32,
            }
        },
        "messaging": {
            "max_message_length": 4000,
        },
        "servers": {
            "max_servers_per_user": 100,
        },
    }
