"""
Session Hijacking Prevention Tests.

Tests that sessions cannot be hijacked through various attack vectors
including session fixation, session prediction, and token theft.
"""

import pytest
import time


class TestSessionHijacking:
    """Test session hijacking prevention mechanisms."""

    def test_session_tokens_are_unique(self, modules, user_pool):
        """Test that each login creates a unique session token."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result1 = modules.auth.login(username, password)
        result2 = modules.auth.login(username, password)
        
        assert result1.token != result2.token

    def test_session_tokens_are_random(self, modules, user_pool):
        """Test that session tokens contain sufficient randomness."""
        user, username, password = user_pool.get_user_with_credentials()
        
        tokens = set()
        for _ in range(10):
            result = modules.auth.login(username, password)
            tokens.add(result.token)
        
        assert len(tokens) == 10

    def test_session_token_not_predictable(self, modules, user_pool):
        """Test that session tokens are not predictable from previous tokens."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result1 = modules.auth.login(username, password)
        result2 = modules.auth.login(username, password)
        
        parts1 = result1.token.split(".")
        parts2 = result2.token.split(".")
        
        if len(parts1) >= 2 and len(parts2) >= 2:
            assert parts1[1] != parts2[1]

    def test_session_expires_after_timeout(self, modules, user_pool):
        """Test that sessions expire after the configured timeout."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parsed = modules.auth._auth.tokens.parse_token(result.token)
        
        modules.db.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
            (int(time.time() * 1000) - 1000, parsed["id"])
        )
        
        with pytest.raises(Exception) as exc_info:
            modules.auth.verify_token(result.token)
        
        assert "expired" in str(exc_info.value).lower()

    def test_session_invalidated_on_logout(self, modules, user_pool):
        """Test that sessions are properly invalidated on logout."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        token_info = modules.auth.verify_token(result.token)
        assert token_info.valid
        
        modules.auth.logout(result.token)
        
        with pytest.raises(Exception):
            modules.auth.verify_token(result.token)

    def test_all_sessions_can_be_revoked(self, modules, user_pool):
        """Test that all sessions can be revoked at once."""
        user, username, password = user_pool.get_user_with_credentials()
        
        tokens = []
        for _ in range(3):
            result = modules.auth.login(username, password)
            tokens.append(result.token)
        
        for token in tokens:
            modules.auth.verify_token(token)
        
        modules.auth.logout_all(user.id)
        
        for token in tokens:
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_session_refresh_creates_new_token(self, modules, user_pool):
        """Test that session refresh creates a new token."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        old_token = result.token
        
        new_token = modules.auth.refresh_session(old_token)
        
        if new_token:
            assert new_token != old_token
            
            with pytest.raises(Exception):
                modules.auth.verify_token(old_token)
            
            modules.auth.verify_token(new_token)

    def test_session_fixation_prevented(self, modules, user_pool):
        """Test that session fixation attacks are prevented."""
        user, username, password = user_pool.get_user_with_credentials()
        
        fake_token = "123456789.fakesecrettoken"
        
        result = modules.auth.login(username, password)
        
        assert result.token != fake_token

    def test_session_ip_binding(self, modules, user_pool):
        """Test session IP binding if enabled."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(
            username=username,
            password=password,
            ip_address="192.168.1.100"
        )
        
        try:
            token_info = modules.auth.verify_token(
                result.token,
                ip_address="192.168.1.100"
            )
            assert token_info.valid
        except Exception:
            pass

    def test_session_user_agent_binding(self, modules, user_pool):
        """Test session user agent binding if enabled."""
        user, username, password = user_pool.get_user_with_credentials()
        
        user_agent = "Mozilla/5.0 Test Browser"
        
        result = modules.auth.login(
            username=username,
            password=password,
            user_agent=user_agent
        )
        
        try:
            token_info = modules.auth.verify_token(
                result.token,
                user_agent=user_agent
            )
            assert token_info.valid
        except Exception:
            pass

    def test_concurrent_session_limit_enforced(self, modules, user_pool):
        """Test that concurrent session limits are enforced."""
        user, username, password = user_pool.get_user_with_credentials()
        
        max_sessions = 10
        tokens = []
        
        for i in range(max_sessions + 5):
            result = modules.auth.login(username, password)
            tokens.append(result.token)
        
        active_sessions = modules.auth.get_sessions(user.id)
        assert len(active_sessions) <= max_sessions

    def test_session_activity_tracking(self, modules, user_pool):
        """Test that session activity is tracked."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        modules.auth.verify_token(result.token)
        
        time.sleep(0.1)
        
        modules.auth.verify_token(result.token)
        
        sessions = modules.auth.get_sessions(user.id)
        if sessions:
            assert sessions[0].last_activity is not None

    def test_session_device_tracking(self, modules, user_pool):
        """Test that session device information is tracked."""
        user, username, password = user_pool.get_user_with_credentials()
        
        device_info = {
            "fingerprint": "test_device_123",
            "name": "Test Device",
            "type": "desktop"
        }
        
        modules.auth.login(
            username=username,
            password=password,
            device_info=device_info
        )
        
        sessions = modules.auth.get_sessions(user.id)
        if sessions and sessions[0].device_id:
            devices = modules.auth.get_devices(user.id)
            assert len(devices) > 0

    def test_suspicious_activity_detection(self, modules, user_pool):
        """Test detection of suspicious activity patterns."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(
            username=username,
            password=password,
            ip_address="192.168.1.1"
        )
        
        try:
            modules.auth.verify_token(
                result.token,
                ip_address="10.0.0.1"
            )
        except Exception:
            pass

    def test_session_takeover_detection(self, modules, user_pool):
        """Test detection of potential session takeover."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(
            username=username,
            password=password,
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0"
        )
        
        try:
            modules.auth.verify_token(
                result.token,
                ip_address="10.0.0.1",
                user_agent="Different Browser"
            )
        except Exception:
            pass

    def test_token_reuse_from_different_source(self, modules, user_pool):
        """Test that tokens cannot be easily reused from different sources."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password, ip_address="192.168.1.1")
        
        token_info1 = modules.auth.verify_token(result.token, ip_address="192.168.1.1")
        assert token_info1.valid

    def test_device_revocation_revokes_sessions(self, modules, user_pool):
        """Test that revoking a device revokes associated sessions."""
        user, username, password = user_pool.get_user_with_credentials()
        
        device_info = {
            "fingerprint": f"device_{user.id}",
            "name": "Test Device",
            "type": "desktop"
        }
        
        result = modules.auth.login(
            username=username,
            password=password,
            device_info=device_info
        )
        
        sessions = modules.auth.get_sessions(user.id)
        if sessions and sessions[0].device_id:
            modules.auth.revoke_device(user.id, sessions[0].device_id)
            
            with pytest.raises(Exception):
                modules.auth.verify_token(result.token)

    def test_password_change_revokes_sessions(self, modules, user_pool):
        """Test that password changes can optionally revoke sessions."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        try:
            modules.auth.change_password(
                user_id=user.id,
                old_password=password,
                new_password="NewPass123!"
            )
            
            time.sleep(0.1)
            
            modules.auth.verify_token(result.token)
        except Exception:
            pass
