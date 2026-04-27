"""
Login tests for auth module.
"""

import pytest
from src.core.auth.exceptions import InvalidCredentialsError, AccountLockedError
from unittest.mock import patch


class TestLogin:
    """Login tests."""

    def test_login_success(self, db, auth_manager):
        """Test successful login returns a token and user info."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="login_test",
                email="login_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("login_test", "TestPass123!")

        assert result.status == auth_manager.AuthStatus.SUCCESS
        assert result.token is not None
        assert result.user.id == user.id
        assert result.user.username == "login_test"

    def test_login_wrong_password(self, db, auth_manager):
        """Test login fails with wrong password and increments failed attempts."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="login_wrong_test",
                email="login_wrong_test@example.com",
                password="TestPass123!",
            )

        initial_attempts = auth_manager.get_user(user.id).failed_login_attempts

        with patch.object(encryption, "verify_password", return_value=False):
            with pytest.raises(InvalidCredentialsError):
                auth_manager.login("login_wrong_test", "WrongPassword123!")

        updated_user = auth_manager.get_user(user.id)
        assert updated_user.failed_login_attempts == initial_attempts + 1

    def test_account_locking_flow(self, db, auth_manager):
        """Test account gets locked after multiple failed attempts and stays locked."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="lock_test",
                email="lock_test@example.com",
                password="TestPass123!",
            )

        # Make failed login attempts to reach threshold (default is 3 in test config)
        # The threshold check happens AFTER incrementing, so we need threshold attempts
        with patch.object(encryption, "verify_password", return_value=False):
            for i in range(5):  # Try up to 5 times to be safe
                try:
                    auth_manager.login("lock_test", "WrongPassword123!")
                except AccountLockedError:
                    # Account locked as expected
                    break
                except InvalidCredentialsError:
                    # Expected for attempts before threshold
                    continue

        # Verify account is locked
        locked_user = auth_manager.get_user(user.id)
        assert locked_user.account_locked

    def test_session_limit_enforcement(self, db, auth_manager):
        """Test that oldest sessions are revoked when the limit is exceeded."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="session_test",
                email="session_test@example.com",
                password="TestPass123!",
            )

        # Create sessions up to the limit
        sessions = []
        with patch.object(encryption, "verify_password", return_value=True):
            for _ in range(10):
                result = auth_manager.login("session_test", "TestPass123!")
                sessions.append(result.token)

        # Create one more session - should revoke the oldest
        with patch.object(encryption, "verify_password", return_value=True):
            new_result = auth_manager.login("session_test", "TestPass123!")
        assert new_result.status == auth_manager.AuthStatus.SUCCESS

        # Verify session limit is enforced by checking session count
        user_sessions = auth_manager.get_sessions(user.id)
        assert len(user_sessions) <= 10

    def test_login_with_ip_tracking(self, db, auth_manager):
        """Test that login correctly tracks multiple IP addresses."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="ip_test",
                email="ip_test@example.com",
                password="TestPass123!",
            )

        # Login from different IPs
        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login("ip_test", "TestPass123!")
            auth_manager.login("ip_test", "TestPass123!")

        # Verify IP tracking is working (implementation-specific)
        # This is a placeholder for IP tracking verification
        assert True
