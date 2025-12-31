"""
Auth test fixtures.

Most fixtures are inherited from the root conftest.py.
This file only contains auth-specific fixtures if needed.
"""



import pytest
import uuid


@pytest.fixture
def fresh_registered_user(modules):
    """Create a truly fresh user for tests that need isolated state."""
    unique_id = uuid.uuid4().hex[:16]
    username = f"fresh_{unique_id}"
    email = f"{username}@example.com"
    password = "TestPass123!"
    
    user = modules.auth.register(
        username=username,
        email=email,
        password=password
    )
    
    return user, modules.auth, username


# Auth-specific fixtures can be added here if needed.
# Most tests should use the shared fixtures from root conftest.py:
# - modules (for modules.auth)
# - user_factory (for creating test users)
# - test_user, test_user_with_token (convenience fixtures)
