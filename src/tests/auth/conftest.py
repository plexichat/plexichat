"""
Auth test fixtures.

Most fixtures are inherited from the root conftest.py.
This file only contains auth-specific fixtures if needed.
"""

import pytest
import uuid

from src.tests._conftest.totp_clock import frozen_totp_time


@pytest.fixture(autouse=True)
def _deterministic_totp_clock():
    """Freeze pyotp's clock so a minted TOTP code cannot age out before verify.

    A 2FA test mints a code and immediately calls ``confirm_2fa`` /
    ``complete_2fa`` / ``disable_2fa``, but both sides read the wall clock.
    On a heavily oversubscribed host the gap can cross the code's validity
    window and the correct code is rejected as "Invalid code". Pinning
    pyotp's clock removes that host-timing dependency; the TOTP replay cache
    still reads the real clock, so replay-window behaviour is unchanged.
    """
    with frozen_totp_time():
        yield


@pytest.fixture
def fresh_registered_user(modules):
    """Create a truly fresh user for tests that need isolated state."""
    unique_id = uuid.uuid4().hex[:16]
    username = f"fresh_{unique_id}"
    email = f"{username}@example.com"
    password = "TestPass123!"

    user = modules.auth.register(username=username, email=email, password=password)

    return user, modules.auth, username


# Auth-specific fixtures can be added here if needed.
# Most tests should use the shared fixtures from root conftest.py:
# - modules (for modules.auth)
# - user_factory (for creating test users)
# - test_user, test_user_with_token (convenience fixtures)
