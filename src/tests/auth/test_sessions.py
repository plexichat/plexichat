"""
Session management tests for auth module.
"""

import pytest
import time
from unittest.mock import patch


class TestSessions:
    """Test session management."""

    def test_verify_valid_token(self, db, auth_manager):
        """Test verifying a valid token."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="session_test",
                email="session_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("session_test", "TestPass123!")

        token_info = auth_manager.verify_token(result.token)

        assert token_info.valid is True
        assert token_info.user_id == user.id
        assert token_info.token_type == "user"

    def test_verify_invalid_token_format(self, db, auth_manager):
        """Test verifying malformed token."""
        from src.core.auth.exceptions import TokenInvalidError

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token("not-a-valid-token")

    def test_verify_token_wrong_secret(self, db, auth_manager):
        """Test verifying token with wrong secret."""
        from src.utils import encryption
        from src.core.auth.exceptions import TokenInvalidError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="secret_test",
                email="secret_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("secret_test", "TestPass123!")

        parts = result.token.split(".")
        fake_token = f"{parts[0]}.wrongsecret"

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(fake_token)

    def test_verify_revoked_token(self, db, auth_manager):
        """Test verifying a revoked token."""
        from src.utils import encryption
        from src.core.auth.exceptions import TokenInvalidError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="revoke_test",
                email="revoke_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            # Create a new session to revoke
            result = auth_manager.login("revoke_test", "TestPass123!")
            auth_manager.logout(result.token)

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(result.token)

    def test_logout_invalidates_session(self, db, auth_manager):
        """Test that logout invalidates the session."""
        from src.utils import encryption
        from src.core.auth.exceptions import TokenInvalidError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="logout_test",
                email="logout_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            # Create new session to logout
            result = auth_manager.login("logout_test", "TestPass123!")

        success = auth_manager.logout(result.token)
        assert success is True

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(result.token)

    def test_logout_returns_false_for_invalid(self, db, auth_manager):
        """Test logout returns False for invalid token."""
        result = auth_manager.logout("invalid.token")
        assert result is False

    def test_logout_all_devices(self, db, auth_manager):
        """Test logging out all devices."""
        from src.utils import encryption
        from src.core.auth.exceptions import TokenInvalidError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="logoutall_test",
                email="logoutall_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result1 = auth_manager.login("logoutall_test", "TestPass123!")
            auth_manager.login("logoutall_test", "TestPass123!")
            auth_manager.login("logoutall_test", "TestPass123!")

        count = auth_manager.logout_all(user.id)
        assert count >= 3

        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(result1.token)

    def test_logout_all_except_current(self, db, auth_manager):
        """Test logging out all except current session."""
        from src.utils import encryption
        from src.core.auth.exceptions import TokenInvalidError

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="logoutexcept_test",
                email="logoutexcept_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result1 = auth_manager.login("logoutexcept_test", "TestPass123!")
            auth_manager.login("logoutexcept_test", "TestPass123!")
            result3 = auth_manager.login("logoutexcept_test", "TestPass123!")

        auth_manager.logout_all(user.id, except_token=result3.token)

        # Current token should still work
        token_info = auth_manager.verify_token(result3.token)
        assert token_info.valid is True

        # Others should be invalid
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(result1.token)

    def test_get_sessions(self, db, auth_manager):
        """Test getting active sessions."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="getsessions_test",
                email="getsessions_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            auth_manager.login("getsessions_test", "TestPass123!")
            auth_manager.login("getsessions_test", "TestPass123!")

        sessions = auth_manager.get_sessions(user.id)
        assert len(sessions) >= 2

    def test_revoke_session(self, db, auth_manager):
        """Test revoking a specific session."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="revoke_session_test",
                email="revoke_session_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("revoke_session_test", "TestPass123!")

        # Parse session ID from token
        session_id = int(result.token.split(".")[0])

        success = auth_manager.revoke_session(user.id, session_id)
        assert success is True

    def test_revoke_session_wrong_user(self, db, auth_manager):
        """Test revoking session of another user fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            auth_manager.register(
                username="revokeuser1_test",
                email="revokeuser1_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="revokeuser2_test",
                email="revokeuser2_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("revokeuser1_test", "TestPass123!")
            session_id = int(result.token.split(".")[0])

        success = auth_manager.revoke_session(user2.id, session_id)
        assert success is False

    def test_session_token_format(self, db, auth_manager):
        """Test session token has correct format."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="format_test",
                email="format_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("format_test", "TestPass123!")

        parts = result.token.split(".")
        assert len(parts) == 2
        assert parts[0].isdigit()
        assert len(parts[1]) > 20

    def test_token_info_contains_permissions(self, db, auth_manager):
        """Test token info includes permissions."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="tokeninfo_test",
                email="tokeninfo_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            result = auth_manager.login("tokeninfo_test", "TestPass123!")

        token_info = auth_manager.verify_token(result.token)

        assert token_info.permissions is not None
        assert isinstance(token_info.permissions, dict)
        assert token_info.permissions.get("messages.send") is True

    def test_session_limit_revokes_oldest(self, db, auth_manager):
        """Test that exceeding session limit revokes oldest."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="sessionlimit_test",
                email="sessionlimit_test@example.com",
                password="TestPass123!",
            )

        with patch.object(encryption, "verify_password", return_value=True):
            # Create many sessions (config has max_per_user = 10)
            for i in range(12):
                auth_manager.login("sessionlimit_test", "TestPass123!")
                time.sleep(0.01)

        sessions = auth_manager.get_sessions(user.id)
        assert len(sessions) <= 10
