"""
API test fixtures.

Uses shared fixtures from root conftest.py for database and modules.
Provides API-specific fixtures for test client and authentication.

NOTE: All fixtures here are function-scoped to match the root conftest's
function-scoped db/modules fixtures. Using module scope would cause
ScopeMismatch errors.
"""

import pytest
import uuid
from unittest.mock import patch
from src.utils import encryption


@pytest.fixture
def second_test_user(modules):
    """Get a second test user for relationship/interaction tests."""
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = modules.auth.register(
            username=f"apiuser2_{uuid.uuid4().hex[:8]}",
            email=f"api2_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )

    with patch.object(encryption, "verify_password", return_value=True):
        result = modules.auth.login(user.username, "TestPass123!")

    return {
        "user": user,
        "token": result.token,
        "username": user.username,
        "password": "TestPass123!",
    }


@pytest.fixture
def auth_headers(test_user_with_token):
    """Get authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {test_user_with_token['token']}"}


@pytest.fixture
def second_auth_headers(second_test_user):
    """Get authorization headers for second user."""
    return {"Authorization": f"Bearer {second_test_user['token']}"}


# Legacy compatibility - some tests use db_and_modules directly
@pytest.fixture
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
