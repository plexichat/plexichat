"""
API test fixtures.

Uses shared fixtures from root conftest.py for database and modules.
Provides API-specific fixtures for test client and authentication.
"""

import pytest
import uuid


@pytest.fixture(scope="module")
def test_user(modules, session_users):
    """
    Create a test user and return credentials dict.

    Uses a user from the session pool for speed.
    Returns dict format expected by API tests.
    """
    # Get a user from the pool (already created with real Argon2)
    user, username, password = session_users[0]

    # Login to get token
    result = modules.auth.login(username, password)

    return {
        "user": user,
        "token": result.token,
        "username": username,
        "password": password,
    }


@pytest.fixture(scope="module")
def second_test_user(modules, session_users):
    """Get a second test user for relationship/interaction tests."""
    user, username, password = session_users[1]
    result = modules.auth.login(username, password)

    return {
        "user": user,
        "token": result.token,
        "username": username,
        "password": password,
    }


@pytest.fixture(scope="module")
def test_server(modules, test_user):
    """Create a test server."""
    unique_id = uuid.uuid4().hex[:8]

    server = modules.servers.create_server(
        owner_id=test_user["user"].id, name=f"Test Server {unique_id}"
    )

    channels = modules.servers.get_channels(test_user["user"].id, server.id)
    channel = channels[0] if channels else None

    return {
        "server": server,
        "channel": channel,
    }


@pytest.fixture
def auth_headers(test_user):
    """Get authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {test_user['token']}"}


@pytest.fixture
def second_auth_headers(second_test_user):
    """Get authorization headers for second user."""
    return {"Authorization": f"Bearer {second_test_user['token']}"}


# Legacy compatibility - some tests use db_and_modules directly
@pytest.fixture(scope="module")
def db_and_modules(modules):
    """
    Legacy fixture for backward compatibility.

    Returns dict format expected by older API tests.
    """
    return {
        "db": modules._db,
        "auth": modules.auth,
        "messaging": modules.messaging,
        "servers": modules.servers,
        "relationships": modules.relationships,
        "presence": modules.presence,
        "reactions": modules.reactions,
        "embeds": modules.embeds,
        "webhooks": modules.webhooks,
    }
