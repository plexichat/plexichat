"""
Session management tests for auth module.
"""

import pytest
import time
import uuid


def unique_name(prefix):
    """Generate a unique username."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class TestSessions:
    """Test session management."""

    def test_verify_valid_token(self, logged_in_user):
        """Test verifying a valid token."""
        user, token, auth, username = logged_in_user

        token_info = auth.verify_token(token)

        assert token_info.valid is True
        assert token_info.user_id == user.id
        assert token_info.token_type == "user"

    def test_verify_invalid_token_format(self, db_and_auth):
        """Test verifying malformed token."""
        db, auth = db_and_auth

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token("not-a-valid-token")

    def test_verify_token_wrong_secret(self, logged_in_user):
        """Test verifying token with wrong secret."""
        user, token, auth, username = logged_in_user

        parts = token.split(".")
        fake_token = f"{parts[0]}.wrongsecret"

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(fake_token)

    def test_verify_revoked_token(self, logged_in_user):
        """Test verifying a revoked token."""
        user, token, auth, username = logged_in_user

        # Create a new session to revoke
        result = auth.login(username, "TestPass123!")
        auth.logout(result.token)

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(result.token)

    def test_logout_invalidates_session(self, logged_in_user):
        """Test that logout invalidates the session."""
        user, token, auth, username = logged_in_user

        # Create new session to logout
        result = auth.login(username, "TestPass123!")

        success = auth.logout(result.token)
        assert success is True

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(result.token)

    def test_logout_returns_false_for_invalid(self, db_and_auth):
        """Test logout returns False for invalid token."""
        db, auth = db_and_auth

        result = auth.logout("invalid.token")
        assert result is False

    def test_logout_all_devices(self, db_and_auth):
        """Test logging out all devices."""
        db, auth = db_and_auth

        name = unique_name("logoutall")
        user = auth.register(name, f"{name}@example.com", "TestPass123!")

        result1 = auth.login(name, "TestPass123!")
        result2 = auth.login(name, "TestPass123!")
        result3 = auth.login(name, "TestPass123!")

        count = auth.logout_all(user.id)
        assert count >= 3

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(result1.token)

    def test_logout_all_except_current(self, db_and_auth):
        """Test logging out all except current session."""
        db, auth = db_and_auth

        name = unique_name("logoutexcept")
        user = auth.register(name, f"{name}@example.com", "TestPass123!")

        result1 = auth.login(name, "TestPass123!")
        result2 = auth.login(name, "TestPass123!")
        result3 = auth.login(name, "TestPass123!")

        auth.logout_all(user.id, except_token=result3.token)

        # Current token should still work
        token_info = auth.verify_token(result3.token)
        assert token_info.valid is True

        # Others should be invalid
        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(result1.token)

    def test_get_sessions(self, registered_user):
        """Test getting active sessions."""
        user, auth, username = registered_user

        auth.login(username, "TestPass123!")
        auth.login(username, "TestPass123!")

        sessions = auth.get_sessions(user.id)
        assert len(sessions) >= 2

    def test_revoke_session(self, registered_user):
        """Test revoking a specific session."""
        user, auth, username = registered_user

        result = auth.login(username, "TestPass123!")

        # Parse session ID from token
        session_id = int(result.token.split(".")[0])

        success = auth.revoke_session(user.id, session_id)
        assert success is True

    def test_revoke_session_wrong_user(self, db_and_auth):
        """Test revoking session of another user fails."""
        db, auth = db_and_auth

        name1 = unique_name("revokeuser1")
        name2 = unique_name("revokeuser2")
        user1 = auth.register(name1, f"{name1}@example.com", "TestPass123!")
        user2 = auth.register(name2, f"{name2}@example.com", "TestPass123!")

        result = auth.login(name1, "TestPass123!")
        session_id = int(result.token.split(".")[0])

        success = auth.revoke_session(user2.id, session_id)
        assert success is False

    def test_session_token_format(self, logged_in_user):
        """Test session token has correct format."""
        user, token, auth, username = logged_in_user

        parts = token.split(".")
        assert len(parts) == 2
        assert parts[0].isdigit()
        assert len(parts[1]) > 20

    def test_token_info_contains_permissions(self, logged_in_user):
        """Test token info includes permissions."""
        user, token, auth, username = logged_in_user

        token_info = auth.verify_token(token)

        assert token_info.permissions is not None
        assert isinstance(token_info.permissions, dict)
        assert token_info.permissions.get("messages.send") is True

    def test_session_limit_revokes_oldest(self, db_and_auth):
        """Test that exceeding session limit revokes oldest."""
        db, auth = db_and_auth

        name = unique_name("sessionlimit")
        user = auth.register(name, f"{name}@example.com", "TestPass123!")

        # Create many sessions (config has max_per_user = 10)
        for i in range(12):
            auth.login(name, "TestPass123!")
            time.sleep(0.01)

        sessions = auth.get_sessions(user.id)
        assert len(sessions) <= 10
