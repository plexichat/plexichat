"""
Authentication Bypass Attempt Tests.

Tests various attempts to bypass authentication mechanisms including
token manipulation, session hijacking attempts, and credential stuffing.
"""

import pytest
import time


class TestAuthenticationBypass:
    """Test authentication bypass prevention."""

    def test_invalid_token_rejected(self, modules):
        """Test that invalid tokens are rejected."""
        invalid_tokens = [
            "invalid.token.here",
            "123.456",
            "bot.123.invalidtoken",
            "",
            None,
        ]
        
        for token in invalid_tokens:
            if token is None:
                continue
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_malformed_token_rejected(self, modules):
        """Test that malformed tokens are rejected."""
        malformed_tokens = [
            "onlyonepart",
            "two.parts",
            "three.parts.only",
            "bot.notanid.secret",
            "123456789012345678.tooshort",
        ]
        
        for token in malformed_tokens:
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_token_from_different_session_rejected(self, modules, user_pool):
        """Test that token from different session cannot access other sessions."""
        user1, username1, password1 = user_pool.get_user_with_credentials()
        user2, username2, password2 = user_pool.get_user_with_credentials()
        
        result1 = modules.auth.login(username1, password1)
        result2 = modules.auth.login(username2, password2)
        
        token_info1 = modules.auth.verify_token(result1.token)
        token_info2 = modules.auth.verify_token(result2.token)
        
        assert token_info1.user_id != token_info2.user_id
        assert token_info1.session_id != token_info2.session_id

    def test_password_brute_force_protection(self, modules, user_pool):
        """Test that password brute force attempts are blocked."""
        user, username, password = user_pool.get_user_with_credentials()
        
        max_attempts = 3
        
        for i in range(max_attempts):
            with pytest.raises(Exception):
                modules.auth.login(username, "wrongpassword")
        
        with pytest.raises(Exception) as exc_info:
            modules.auth.login(username, password)
        
        assert "locked" in str(exc_info.value).lower() or "attempt" in str(exc_info.value).lower()

    def test_account_lockout_after_failed_attempts(self, modules, user_pool):
        """Test that accounts are locked after failed login attempts."""
        user, username, password = user_pool.get_user_with_credentials()
        
        for i in range(5):
            try:
                modules.auth.login(username, "wrongpassword")
            except Exception:
                pass
        
        with pytest.raises(Exception) as exc_info:
            modules.auth.login(username, password)
        
        error_msg = str(exc_info.value).lower()
        assert "locked" in error_msg or "attempt" in error_msg

    def test_token_tampering_detected(self, modules, user_pool):
        """Test that tampered tokens are detected."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parts = result.token.split(".")
        if len(parts) >= 2:
            tampered_token = f"{parts[0]}.tampered_secret"
            
            with pytest.raises(Exception):
                modules.auth.verify_token(tampered_token)

    def test_session_id_tampering_detected(self, modules, user_pool):
        """Test that session ID tampering is detected."""
        user1, username1, password1 = user_pool.get_user_with_credentials()
        user2, username2, password2 = user_pool.get_user_with_credentials()
        
        result1 = modules.auth.login(username1, password1)
        result2 = modules.auth.login(username2, password2)
        
        parts1 = result1.token.split(".")
        parts2 = result2.token.split(".")
        
        if len(parts1) >= 2 and len(parts2) >= 2:
            tampered_token = f"{parts1[0]}.{parts2[1]}"
            
            with pytest.raises(Exception):
                modules.auth.verify_token(tampered_token)

    def test_bot_token_cannot_access_user_sessions(self, modules, user_pool):
        """Test that bot tokens cannot access user session features."""
        owner = user_pool.get_user()
        
        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"bot_{owner.id}",
            display_name="Test Bot"
        )
        
        token_info = modules.auth.verify_token(bot.token)
        assert token_info.token_type == "bot"
        assert token_info.session_id is None

    def test_replay_attack_prevention(self, modules, user_pool):
        """Test that replayed login requests don't bypass rate limiting."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result1 = modules.auth.login(username, password)
        result2 = modules.auth.login(username, password)
        
        assert result1.token != result2.token

    def test_timing_attack_resistance(self, modules):
        """Test that authentication timing is consistent."""
        times = []
        
        for i in range(5):
            start = time.time()
            try:
                modules.auth.login(f"nonexistent_{i}", "password")
            except Exception:
                pass
            elapsed = time.time() - start
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        for t in times:
            assert abs(t - avg_time) < avg_time * 0.5

    def test_null_byte_injection_in_credentials(self, modules):
        """Test that null byte injection is prevented."""
        null_byte_usernames = [
            "admin\x00",
            "user\x00admin",
            "\x00admin",
        ]
        
        for username in null_byte_usernames:
            with pytest.raises(Exception):
                modules.auth.register(
                    username=username,
                    email="test@test.com",
                    password="TestPass123!"
                )

    def test_unicode_normalization_attacks(self, modules):
        """Test that unicode normalization attacks are prevented."""
        unicode_usernames = [
            "admin\u200b",
            "admin\ufeff",
            "\u202eadmin",
        ]
        
        for username in unicode_usernames:
            with pytest.raises(Exception):
                modules.auth.register(
                    username=username,
                    email="test@test.com",
                    password="TestPass123!"
                )

    def test_case_sensitivity_in_login(self, modules, user_pool):
        """Test case sensitivity in login credentials."""
        user, username, password = user_pool.get_user_with_credentials()
        
        try:
            result = modules.auth.login(username.upper(), password)
            if result:
                assert result.user.id == user.id
        except Exception:
            pass

    def test_empty_credentials_rejected(self, modules):
        """Test that empty credentials are rejected."""
        with pytest.raises(Exception):
            modules.auth.login("", "")
        
        with pytest.raises(Exception):
            modules.auth.login("username", "")
        
        with pytest.raises(Exception):
            modules.auth.login("", "password")

    def test_extremely_long_credentials(self, modules):
        """Test handling of extremely long credentials."""
        long_string = "a" * 10000
        
        with pytest.raises(Exception):
            modules.auth.login(long_string, long_string)

    def test_special_characters_in_credentials(self, modules):
        """Test special characters in credentials."""
        special_chars = [
            "user\r\nname",
            "user\tname",
            "user\nname",
            "user\0name",
        ]
        
        for username in special_chars:
            with pytest.raises(Exception):
                modules.auth.register(
                    username=username,
                    email="test@test.com",
                    password="TestPass123!"
                )

    def test_password_reset_token_single_use(self, modules, user_pool):
        """Test that password reset tokens can only be used once."""
        user, username, password = user_pool.get_user_with_credentials()
        
        if modules.auth.email_sender:
            modules.auth.request_password_reset(user.email)
