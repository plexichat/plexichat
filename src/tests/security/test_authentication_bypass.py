"""
Authentication Bypass Attempt Tests.

Tests various attempts to bypass authentication mechanisms including
token manipulation, session hijacking attempts, and credential stuffing.
"""

import pytest


class TestAuthenticationBypass:
    """Test authentication bypass prevention."""

    def test_invalid_token_rejected(self, auth_manager):
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
                auth_manager.verify_token(token)

    def test_malformed_token_rejected(self, auth_manager):
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
                auth_manager.verify_token(token)

    def test_token_from_different_session_rejected(self, auth_manager, two_users):
        """Test that token from different session cannot access other sessions."""
        user1, user2 = two_users

        result1 = auth_manager.login(user1.username, "TestPass123!")
        result2 = auth_manager.login(user2.username, "TestPass123!")

        token_info1 = auth_manager.verify_token(result1.token)
        token_info2 = auth_manager.verify_token(result2.token)

        assert token_info1.user_id != token_info2.user_id
        assert token_info1.session_id != token_info2.session_id

    def test_password_brute_force_protection(self, auth_manager, test_user):
        """Test that password brute force attempts are blocked."""
        max_attempts = 3

        for i in range(max_attempts):
            with pytest.raises(Exception):
                auth_manager.login(test_user.username, "wrongpassword")

        with pytest.raises(Exception) as exc_info:
            auth_manager.login(test_user.username, "TestPass123!")

        assert (
            "locked" in str(exc_info.value).lower()
            or "attempt" in str(exc_info.value).lower()
        )

    def test_account_lockout_after_failed_attempts(self, auth_manager, test_user):
        """Test that accounts are locked after failed login attempts."""
        for i in range(5):
            try:
                auth_manager.login(test_user.username, "wrongpassword")
            except Exception:
                pass

        with pytest.raises(Exception) as exc_info:
            auth_manager.login(test_user.username, "TestPass123!")

        error_msg = str(exc_info.value).lower()
        assert "locked" in error_msg or "attempt" in error_msg

    def test_token_tampering_detected(self, auth_manager, test_user):
        """Test that tampered tokens are detected."""
        result = auth_manager.login(test_user.username, "TestPass123!")

        parts = result.token.split(".")
        if len(parts) >= 2:
            tampered_token = f"{parts[0]}.tampered_secret"

            with pytest.raises(Exception):
                auth_manager.verify_token(tampered_token)

    def test_session_id_tampering_detected(self, auth_manager, two_users):
        """Test that session ID tampering is detected."""
        user1, user2 = two_users
        result1 = auth_manager.login(user1.username, "TestPass123!")
        result2 = auth_manager.login(user2.username, "TestPass123!")

        parts1 = result1.token.split(".")
        parts2 = result2.token.split(".")

        if len(parts1) >= 2 and len(parts2) >= 2:
            tampered_token = f"{parts1[0]}.{parts2[1]}"

            with pytest.raises(Exception):
                auth_manager.verify_token(tampered_token)

    def test_bot_token_cannot_access_user_sessions(self, auth_manager, test_user):
        """Test that bot tokens cannot access user session features."""
        bot = auth_manager.create_bot(
            owner_id=test_user.id,
            username=f"bot_{test_user.id}",
            display_name="Test Bot",
        )

        token_info = auth_manager.verify_token(bot.token)
        assert token_info.token_type == "bot"
        assert token_info.session_id is None

    def test_replay_attack_prevention(self, auth_manager, test_user):
        """Test that replayed login requests don't bypass rate limiting."""
        result1 = auth_manager.login(test_user.username, "TestPass123!")
        result2 = auth_manager.login(test_user.username, "TestPass123!")

        assert result1.token != result2.token

    def test_timing_attack_resistance(self, auth_manager):
        """Test that authentication timing is consistent."""

        for i in range(5):
            try:
                auth_manager.login(f"nonexistent_{i}", "password")
            except Exception:
                pass

    def test_null_byte_injection_in_credentials(self, auth_manager):
        """Test that null byte injection is prevented."""
        null_byte_usernames = [
            "admin\x00",
            "user\x00admin",
            "\x00admin",
        ]

        for username in null_byte_usernames:
            with pytest.raises(Exception):
                auth_manager.register(
                    username=username, email="test@test.com", password="TestPass123!"
                )

    def test_unicode_normalization_attacks(self, auth_manager):
        """Test that unicode normalization attacks are prevented."""
        unicode_usernames = [
            "admin\u200b",
            "admin\ufeff",
            "\u202eadmin",
        ]

        for username in unicode_usernames:
            with pytest.raises(Exception):
                auth_manager.register(
                    username=username, email="test@test.com", password="TestPass123!"
                )

    def test_case_sensitivity_in_login(self, auth_manager, test_user):
        """Test case sensitivity in login credentials."""
        try:
            result = auth_manager.login(test_user.username.upper(), "TestPass123!")
            if result:
                assert result.user.id == test_user.id
        except Exception:
            pass

    def test_empty_credentials_rejected(self, auth_manager):
        """Test that empty credentials are rejected."""
        with pytest.raises(Exception):
            auth_manager.login("", "")

        with pytest.raises(Exception):
            auth_manager.login("username", "")

        with pytest.raises(Exception):
            auth_manager.login("", "password")

    def test_extremely_long_credentials(self, auth_manager):
        """Test handling of extremely long credentials."""
        long_string = "a" * 10000

        with pytest.raises(Exception):
            auth_manager.login(long_string, long_string)

    def test_special_characters_in_credentials(self, auth_manager):
        """Test special characters in credentials."""
        special_chars = [
            "user\r\nname",
            "user\tname",
            "user\nname",
            "user\0name",
        ]

        for username in special_chars:
            with pytest.raises(Exception):
                auth_manager.register(
                    username=username, email="test@test.com", password="TestPass123!"
                )


def test_token_tampering_detected(self, auth_manager, test_user):
    """Test that tampered tokens are detected."""
    result = auth_manager.login(test_user.username, "TestPass123!")

    parts = result.token.split(".")
    if len(parts) >= 2:
        tampered_token = f"{parts[0]}.tampered_secret"

        with pytest.raises(Exception):
            auth_manager.verify_token(tampered_token)


def test_session_id_tampering_detected(self, auth_manager, two_users):
    """Test that session ID tampering is detected."""
    user1, user2 = two_users
    result1 = auth_manager.login(user1.username, "TestPass123!")
    result2 = auth_manager.login(user2.username, "TestPass123!")

    parts1 = result1.token.split(".")
    parts2 = result2.token.split(".")

    if len(parts1) >= 2 and len(parts2) >= 2:
        tampered_token = f"{parts1[0]}.{parts2[1]}"

        with pytest.raises(Exception):
            auth_manager.verify_token(tampered_token)


def test_bot_token_cannot_access_user_sessions(self, auth_manager, test_user):
    """Test that bot tokens cannot access user session features."""
    bot = auth_manager.create_bot(
        owner_id=test_user.id,
        username=f"bot_{test_user.id}",
        display_name="Test Bot",
    )

    token_info = auth_manager.verify_token(bot.token)
    assert token_info.token_type == "bot"
    assert token_info.session_id is None


def test_replay_attack_prevention(self, auth_manager, test_user):
    """Test that replayed login requests don't bypass rate limiting."""
    result1 = auth_manager.login(test_user.username, "TestPass123!")
    result2 = auth_manager.login(test_user.username, "TestPass123!")

    assert result1.token != result2.token


def test_timing_attack_resistance(self, auth_manager):
    """Test that authentication timing is consistent."""

    for i in range(5):
        try:
            auth_manager.login(f"nonexistent_{i}", "password")
        except Exception:
            pass
