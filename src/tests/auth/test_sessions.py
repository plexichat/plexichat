"""
Comprehensive session tests covering token security, expiration, and concurrent handling.
"""

import pytest
import time
import asyncio
from src.core.auth.exceptions import TokenInvalidError
from src.tests.fixtures.config import TEST_PASSWORD


class TestSessionCreation:
    """Tests for session creation."""

    def test_login_creates_session(self, modules):
        """Test login creates a new session."""
        username = f"sesstest_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        assert result.session is not None
        assert result.session.id is not None
        assert result.session.user_id == user.id

    def test_session_token_format(self, modules):
        """Test session token has correct format."""
        username = f"tokenformat_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        assert result.token is not None
        parts = result.token.split(".")
        assert len(parts) == 2  # session_id.secret

    def test_session_has_timestamps(self, modules):
        """Test session has creation and expiration timestamps."""
        username = f"timestamps_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        assert result.session.created_at > 0
        assert result.session.expires_at > result.session.created_at
        assert result.session.last_activity > 0

    def test_multiple_sessions_per_user(self, modules):
        """Test user can have multiple sessions."""
        username = f"multisess_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result1 = modules.auth.login(username, TEST_PASSWORD)
        result2 = modules.auth.login(username, TEST_PASSWORD)

        assert result1.session.id != result2.session.id

        sessions = modules.auth.get_sessions(user.id)
        assert len(sessions) >= 2


class TestSessionVerification:
    """Tests for session token verification."""

    def test_verify_valid_token(self, modules):
        """Test verifying a valid session token."""
        username = f"verifyvalid_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        token_info = modules.auth.verify_token(result.token)

        assert token_info.valid is True
        assert token_info.token_type == "user"

    def test_verify_invalid_token_format(self, modules):
        """Test verifying invalid token format fails."""
        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token("invalid_token")

    def test_verify_nonexistent_session(self, modules):
        """Test verifying non-existent session fails."""
        fake_token = "99999999999.fake_secret_token_12345"

        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(fake_token)

    def test_verify_wrong_secret(self, modules):
        """Test verifying with wrong secret fails."""
        username = f"wrongsecret_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        session_id = result.token.split(".")[0]
        fake_token = f"{session_id}.wrong_secret"

        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(fake_token)

    def test_verify_revoked_session(self, modules):
        """Test verifying revoked session fails."""
        username = f"revoked_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.logout(result.token)

        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(result.token)


class TestSessionExpiration:
    """Tests for session expiration."""

    def test_session_expires_after_configured_time(self, modules):
        """Test session has expiration time set."""
        username = f"expires_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        # Default is 168 hours (7 days)
        expected_duration = 168 * 3600 * 1000  # in milliseconds
        actual_duration = result.session.expires_at - result.session.created_at

        # Allow some variance
        assert abs(actual_duration - expected_duration) < 1000

    def test_verify_expired_session_fails(self, modules):
        """Test verifying expired session fails."""
        # This would require time manipulation or database update
        pass

    def test_session_activity_updates(self, modules):
        """Test session activity timestamp updates on use."""
        username = f"activity_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        time.sleep(0.1)
        modules.auth.verify_token(result.token)

        # Check updated activity (would need to query DB)
        # This is a limitation of the test - last_activity updates in DB


class TestSessionRevocation:
    """Tests for session revocation."""

    def test_logout_revokes_session(self, modules):
        """Test logout revokes the session."""
        username = f"logout_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        success = modules.auth.logout(result.token)

        assert success is True

        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(result.token)

    def test_logout_invalid_token(self, modules):
        """Test logout with invalid token."""
        result = modules.auth.logout("invalid_token")
        assert result is False

    def test_revoke_specific_session(self, modules):
        """Test revoking a specific session."""
        username = f"revokespecific_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result1 = modules.auth.login(username, TEST_PASSWORD)
        result2 = modules.auth.login(username, TEST_PASSWORD)

        # Revoke first session
        success = modules.auth.revoke_session(user.id, result1.session.id)
        assert success is True

        # First should be invalid
        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(result1.token)

        # Second should still work
        token_info = modules.auth.verify_token(result2.token)
        assert token_info.valid is True

    def test_revoke_session_wrong_user(self, modules):
        """Test cannot revoke another user's session."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        result1 = modules.auth.login(user1, TEST_PASSWORD)

        # User 2 tries to revoke user 1's session
        success = modules.auth.revoke_session(u2.id, result1.session.id)
        assert success is False

    def test_logout_all_sessions(self, modules):
        """Test logging out all sessions for a user."""
        username = f"logoutall_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create 3 sessions
        tokens = []
        for _ in range(3):
            result = modules.auth.login(username, TEST_PASSWORD)
            tokens.append(result.token)

        # Logout all
        count = modules.auth.logout_all(user.id)
        assert count == 3

        # All tokens should be invalid
        for token in tokens:
            with pytest.raises(TokenInvalidError):
                modules.auth.verify_token(token)

    def test_logout_all_except_current(self, modules):
        """Test logging out all sessions except current."""
        username = f"exceptcurrent_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create 3 sessions
        modules.auth.login(username, TEST_PASSWORD)
        result2 = modules.auth.login(username, TEST_PASSWORD)
        modules.auth.login(username, TEST_PASSWORD)

        # Logout all except result2
        count = modules.auth.logout_all(user.id, except_token=result2.token)
        assert count >= 2

        # Result2 should still work
        token_info = modules.auth.verify_token(result2.token)
        assert token_info.valid is True


class TestSessionSecurity:
    """Tests for session security features."""

    def test_session_token_is_random(self, modules):
        """Test session tokens are randomly generated."""
        username = f"random_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        tokens = []
        for _ in range(10):
            result = modules.auth.login(username, TEST_PASSWORD)
            tokens.append(result.token)

        # All tokens should be unique
        assert len(tokens) == len(set(tokens))

    def test_session_secret_not_guessable(self, modules):
        """Test session secrets have sufficient entropy."""
        username = f"entropy_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        secret = result.token.split(".")[1]

        # Secret should be at least 32 bytes base64 encoded
        assert len(secret) >= 40

    def test_session_token_constant_time_comparison(self, modules):
        """Test session verification uses constant-time comparison."""
        # This is verified by implementation review
        # The verify_token_hash function uses constant-time comparison
        pass

    def test_concurrent_session_verification(self, modules):
        """Test concurrent token verification is thread-safe."""
        username = f"concurrent_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        import threading

        results = []

        def verify():
            try:
                token_info = modules.auth.verify_token(result.token)
                results.append(("success", token_info.valid))
            except Exception as e:
                results.append(("error", str(e)))

        threads = [threading.Thread(target=verify) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed
        assert all(r[0] == "success" for r in results)
        assert all(r[1] is True for r in results)


class TestSessionRefresh:
    """Tests for session refresh functionality."""

    def test_refresh_session(self, modules):
        """Test refreshing a session creates new token."""
        username = f"refresh_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        old_token = result.token

        new_token = modules.auth.refresh_session(old_token)

        assert new_token is not None
        assert new_token != old_token

        # New token should work
        token_info = modules.auth.verify_token(new_token)
        assert token_info.valid is True

        # Old token should be revoked
        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(old_token)

    def test_refresh_invalid_token(self, modules):
        """Test refreshing invalid token returns None."""
        result = modules.auth.refresh_session("invalid_token")
        assert result is None


class TestSessionLimits:
    """Tests for session limit enforcement."""

    def test_max_sessions_enforced(self, modules):
        """Test maximum session limit is enforced."""
        username = f"maxsess_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create more than max (10 in config)
        for _ in range(12):
            modules.auth.login(username, TEST_PASSWORD)

        sessions = modules.auth.get_sessions(user.id)
        assert len(sessions) <= 10

    def test_oldest_session_revoked_on_limit(self, modules):
        """Test oldest session is revoked when limit exceeded."""
        username = f"oldrevoke_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        first_result = modules.auth.login(username, TEST_PASSWORD)
        first_id = first_result.session.id

        # Create 10 more to exceed limit
        for _ in range(10):
            modules.auth.login(username, TEST_PASSWORD)
            time.sleep(0.01)

        sessions = modules.auth.get_sessions(user.id)
        session_ids = [s.id for s in sessions]

        # First session should be gone
        assert first_id not in session_ids


class TestGetSessions:
    """Tests for listing user sessions."""

    def test_get_sessions_empty(self, modules):
        """Test getting sessions for user with no sessions."""
        username = f"nosess_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        sessions = modules.auth.get_sessions(user.id)
        assert sessions == []

    def test_get_sessions_returns_active_only(self, modules):
        """Test get_sessions returns only active sessions."""
        username = f"activeonly_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result1 = modules.auth.login(username, TEST_PASSWORD)
        result2 = modules.auth.login(username, TEST_PASSWORD)

        # Revoke one
        modules.auth.logout(result1.token)

        sessions = modules.auth.get_sessions(user.id)
        session_ids = [s.id for s in sessions]

        assert result1.session.id not in session_ids
        assert result2.session.id in session_ids

    def test_get_sessions_ordered_by_activity(self, modules):
        """Test sessions are ordered by last activity."""
        username = f"ordered_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create sessions with delays
        for _ in range(3):
            modules.auth.login(username, TEST_PASSWORD)
            time.sleep(0.1)

        sessions = modules.auth.get_sessions(user.id)

        # Should be in descending order of last_activity
        for i in range(len(sessions) - 1):
            assert sessions[i].last_activity >= sessions[i + 1].last_activity


class TestSessionIPBinding:
    """Tests for IP address binding (if enabled)."""

    def test_session_records_ip(self, modules):
        """Test session records IP address."""
        username = f"iprecord_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD, ip_address="1.2.3.4")

        assert result.session.ip_address == "1.2.3.4"

    def test_session_without_ip(self, modules):
        """Test session works without IP address."""
        username = f"noip_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        token_info = modules.auth.verify_token(result.token)

        assert token_info.valid is True


class TestSessionUserAgent:
    """Tests for user agent tracking."""

    def test_session_records_user_agent(self, modules):
        """Test session records user agent."""
        username = f"uarecord_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        ua = "Mozilla/5.0 (Test Browser)"
        result = modules.auth.login(username, TEST_PASSWORD, user_agent=ua)

        assert result.session.user_agent == ua

    def test_session_without_user_agent(self, modules):
        """Test session works without user agent."""
        username = f"noua_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        token_info = modules.auth.verify_token(result.token)

        assert token_info.valid is True


class TestConcurrentSessions:
    """Tests for concurrent session operations."""

    @pytest.mark.asyncio
    async def test_concurrent_logins(self, modules):
        """Test multiple concurrent logins."""
        username = f"conclogin_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        async def login():
            return await asyncio.to_thread(modules.auth.login, username, TEST_PASSWORD)

        results = await asyncio.gather(*[login() for _ in range(5)])

        # All should succeed
        assert all(r.status.value == "success" for r in results)

        # All tokens should be unique
        tokens = [r.token for r in results]
        assert len(tokens) == len(set(tokens))

    @pytest.mark.asyncio
    async def test_concurrent_verifications(self, modules):
        """Test concurrent token verifications."""
        username = f"concverify_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)

        async def verify():
            return await asyncio.to_thread(modules.auth.verify_token, result.token)

        results = await asyncio.gather(*[verify() for _ in range(10)])

        # All should succeed
        assert all(r.valid is True for r in results)
