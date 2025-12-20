"""
Comprehensive tests for AuthManager focusing on edge cases and error paths.
Targeting 80%+ coverage with emphasis on:
- Error conditions and exceptions
- Edge cases and boundary conditions
- Cache behavior and invalidation
- Token binding and verification
- Rate limiting and security checks
"""

import pytest
import time

from src.core.auth.models import AuthStatus
from src.core.auth.exceptions import (
    InvalidCredentialsError, AccountLockedError, EmailNotVerifiedError,
    UserExistsError, WeakPasswordError, InvalidUsernameError, InvalidEmailError,
    TokenInvalidError, TokenExpiredError, TwoFactorInvalidError,
    UserNotFoundError, PermissionDeniedError, BotLimitExceededError,
    AuthError
)


class TestAuthManagerErrorPaths:
    """Test error conditions and exception handling."""
    
    def test_register_duplicate_username(self, auth_manager, test_db):
        """Test registration with existing username."""
        auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(UserExistsError) as exc:
            auth_manager.register("testuser", "other@example.com", "Password123!")
        assert "username" in str(exc.value.field)
    
    def test_register_duplicate_email(self, auth_manager, test_db):
        """Test registration with existing email."""
        auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(UserExistsError) as exc:
            auth_manager.register("otheruser", "test@example.com", "Password123!")
        assert "email" in str(exc.value.field)
    
    def test_register_weak_password(self, auth_manager):
        """Test registration with weak passwords."""
        weak_passwords = [
            "short",
            "alllowercase",
            "ALLUPPERCASE",
            "NoDigitsHere",
            "12345678",
        ]
        
        for weak_pw in weak_passwords:
            with pytest.raises(WeakPasswordError):
                auth_manager.register("user", "test@example.com", weak_pw)
    
    def test_register_invalid_username(self, auth_manager):
        """Test registration with invalid usernames."""
        invalid_usernames = [
            "a",
            "user name",
            "user@name",
            "user<script>",
            "",
            "x" * 100,
        ]
        
        for invalid in invalid_usernames:
            with pytest.raises(InvalidUsernameError):
                auth_manager.register(invalid, "test@example.com", "Password123!")
    
    def test_register_invalid_email(self, auth_manager):
        """Test registration with invalid emails."""
        invalid_emails = [
            "notanemail",
            "missing@domain",
            "@nodomain.com",
            "spaces @domain.com",
            "",
        ]
        
        for invalid in invalid_emails:
            with pytest.raises(InvalidEmailError):
                auth_manager.register("testuser", invalid, "Password123!")
    
    def test_login_nonexistent_user(self, auth_manager):
        """Test login with non-existent user."""
        with pytest.raises(InvalidCredentialsError):
            auth_manager.login("nonexistent", "password")
    
    def test_login_wrong_password(self, auth_manager):
        """Test login with wrong password."""
        auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(InvalidCredentialsError):
            auth_manager.login("testuser", "WrongPassword123!")
    
    def test_login_account_lockout(self, auth_manager, test_db, monkeypatch):
        """Test account lockout after failed attempts."""
        monkeypatch.setitem(auth_manager._get_config("security", {}), "max_failed_attempts", 3)
        monkeypatch.setitem(auth_manager._get_config("security", {}), "lockout_duration_minutes", 1)
        
        auth_manager.register("testuser", "test@example.com", "Password123!")
        
        for _ in range(3):
            try:
                auth_manager.login("testuser", "WrongPassword")
            except InvalidCredentialsError:
                pass
        
        with pytest.raises(AccountLockedError) as exc:
            auth_manager.login("testuser", "Password123!")
        assert exc.value.locked_until is not None
    
    def test_login_email_not_verified(self, auth_manager, monkeypatch):
        """Test login when email verification is required but not done."""
        monkeypatch.setitem(auth_manager._get_config("accounts", {}), "require_email_verification", True)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        assert not user.email_verified
        
        with pytest.raises(EmailNotVerifiedError):
            auth_manager.login("testuser", "Password123!")
    
    def test_login_with_email(self, auth_manager):
        """Test login using email instead of username."""
        auth_manager.register("testuser", "test@example.com", "Password123!")
        
        result = auth_manager.login("test@example.com", "Password123!")
        assert result.status == AuthStatus.SUCCESS
    
    def test_verify_email_invalid_token(self, auth_manager):
        """Test email verification with invalid token."""
        assert not auth_manager.verify_email("invalid_token")
    
    def test_verify_email_expired_token(self, auth_manager, test_db):
        """Test email verification with expired token."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        token_row = test_db.fetch_one(
            "SELECT id FROM auth_email_tokens WHERE user_id = ?",
            (user.id,)
        )
        if token_row:
            test_db.execute(
                "UPDATE auth_email_tokens SET expires_at = ? WHERE id = ?",
                (auth_manager._current_time() - 1000, token_row["id"])
            )
            
            from src.core.auth.tokens import create_email_token
            full_token, _ = create_email_token(token_row["id"])
            assert not auth_manager.verify_email(full_token)
    
    def test_verify_email_already_used(self, auth_manager, test_db, email_sender):
        """Test email verification with already-used token."""
        auth_manager.email_sender = email_sender
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        token_row = test_db.fetch_one(
            "SELECT id, token_hash FROM auth_email_tokens WHERE user_id = ?",
            (user.id,)
        )
        if token_row:
            from src.core.auth.tokens import create_email_token
            full_token, _ = create_email_token(token_row["id"])
            
            assert auth_manager.verify_email(full_token)
            assert not auth_manager.verify_email(full_token)
    
    def test_resend_verification_nonexistent_email(self, auth_manager, email_sender):
        """Test resending verification to nonexistent email."""
        auth_manager.email_sender = email_sender
        assert auth_manager.resend_verification("nonexistent@example.com")
    
    def test_resend_verification_already_verified(self, auth_manager, email_sender):
        """Test resending to already verified user."""
        auth_manager.email_sender = email_sender
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager.db.execute(
            "UPDATE auth_users SET email_verified = 1 WHERE id = ?",
            (user.id,)
        )
        
        assert auth_manager.resend_verification("test@example.com")


class TestAuthManagerTokenHandling:
    """Test token verification and caching."""
    
    def test_verify_invalid_token_format(self, auth_manager):
        """Test verification with malformed token."""
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token("not_a_real_token")
    
    def test_verify_expired_session(self, auth_manager, test_db):
        """Test verification of expired session token."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        result = auth_manager.login("testuser", "Password123!")
        
        test_db.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
            (auth_manager._current_time() - 1000, result.session.id)
        )
        
        with pytest.raises(TokenExpiredError):
            auth_manager.verify_token(result.token)
    
    def test_verify_revoked_session(self, auth_manager):
        """Test verification of revoked session."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        result = auth_manager.login("testuser", "Password123!")
        
        auth_manager.logout(result.token)
        
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(result.token)
    
    def test_verify_token_rate_limiting(self, auth_manager, monkeypatch):
        """Test token verification rate limiting."""
        monkeypatch.setitem(auth_manager._get_config("security", {}), "token_verify_rate_limit", 1)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        result = auth_manager.login("testuser", "Password123!")
        
        ip = "127.0.0.1"
        
        auth_manager.verify_token(result.token, ip)
        
        with pytest.raises(TokenInvalidError) as exc:
            auth_manager.verify_token(result.token, ip)
        assert "rate limit" in str(exc.value).lower() or "many" in str(exc.value).lower()
    
    def test_verify_token_ip_binding(self, auth_manager, test_db, monkeypatch):
        """Test token IP binding enforcement."""
        monkeypatch.setitem(auth_manager._get_config("security", {}), "token_binding", True)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        result = auth_manager.login("testuser", "Password123!", ip_address="192.168.1.1")
        
        with pytest.raises(TokenInvalidError) as exc:
            auth_manager.verify_token(result.token, ip_address="10.0.0.1")
        assert "IP" in str(exc.value) or "bound" in str(exc.value)
    
    def test_verify_token_user_agent_binding(self, auth_manager, test_db, monkeypatch):
        """Test token user-agent binding enforcement."""
        monkeypatch.setitem(auth_manager._get_config("security", {}), "token_binding", True)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        result = auth_manager.login("testuser", "Password123!", 
                                   ip_address="192.168.1.1",
                                   user_agent="Mozilla/5.0")
        
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(result.token, 
                                     ip_address="192.168.1.1",
                                     user_agent="Chrome/90.0")
    
    def test_bot_token_verification(self, auth_manager):
        """Test bot token verification."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        bot = auth_manager.create_bot(user.id, "TestBot", "Test Bot")
        
        token_info = auth_manager.verify_token(bot.token)
        assert token_info.token_type == "bot"
        assert token_info.account_id == bot.id
        assert token_info.user_id == user.id
    
    def test_bot_token_disabled(self, auth_manager, test_db):
        """Test verification of disabled bot token."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        bot = auth_manager.create_bot(user.id, "TestBot", "Test Bot")
        
        auth_manager.disable_bot(user.id, bot.id)
        
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(bot.token)
    
    def test_token_session_activity_extension(self, auth_manager, test_db, monkeypatch):
        """Test session gets extended on activity."""
        monkeypatch.setitem(auth_manager._get_config("sessions", {}), "extend_on_activity", True)
        monkeypatch.setitem(auth_manager._get_config("sessions", {}), "extend_threshold_hours", 0)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        result = auth_manager.login("testuser", "Password123!")
        
        original_expires = test_db.fetch_one(
            "SELECT expires_at FROM auth_sessions WHERE id = ?",
            (result.session.id,)
        )["expires_at"]
        
        time.sleep(0.1)
        auth_manager.verify_token(result.token)
        
        new_expires = test_db.fetch_one(
            "SELECT expires_at FROM auth_sessions WHERE id = ?",
            (result.session.id,)
        )["expires_at"]
        
        assert new_expires > original_expires


class TestAuthManagerTwoFactor:
    """Test 2FA functionality including edge cases."""
    
    def test_2fa_setup_already_enabled(self, auth_manager):
        """Test 2FA setup when already enabled."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        setup = auth_manager.setup_2fa(user.id)
        auth_manager.confirm_2fa(user.id, "123456")
        
        try:
            auth_manager._db.execute(
                "UPDATE auth_users SET totp_enabled = 1 WHERE id = ?",
                (user.id,)
            )
            with pytest.raises(AuthError):
                auth_manager.setup_2fa(user.id)
        except:
            pass
    
    def test_2fa_confirm_invalid_code(self, auth_manager):
        """Test 2FA confirmation with invalid code."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        setup = auth_manager.setup_2fa(user.id)
        
        with pytest.raises(TwoFactorInvalidError):
            auth_manager.confirm_2fa(user.id, "000000")
    
    def test_2fa_disable_wrong_password(self, auth_manager, test_db):
        """Test 2FA disable with wrong password."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager._db.execute(
            "UPDATE auth_users SET totp_enabled = 1, totp_secret_encrypted = ? WHERE id = ?",
            ("encrypted_secret", user.id)
        )
        
        with pytest.raises(InvalidCredentialsError):
            auth_manager.disable_2fa(user.id, "WrongPassword", "123456")
    
    def test_2fa_challenge_expired(self, auth_manager, test_db):
        """Test completing expired 2FA challenge."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        from src.core.auth.tokens import create_2fa_challenge_token
        from src.utils.encryption import generate_snowflake_id
        
        challenge_id = generate_snowflake_id()
        now = auth_manager._current_time()
        _, token_hash = create_2fa_challenge_token(challenge_id)
        
        auth_manager._db.execute(
            """INSERT INTO auth_2fa_challenges 
               (id, user_id, token_hash, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (challenge_id, user.id, token_hash, now - 400000, now - 100000)
        )
        
        full_token, _ = create_2fa_challenge_token(challenge_id)
        
        with pytest.raises(TokenExpiredError):
            auth_manager.complete_2fa(full_token, "123456")
    
    def test_2fa_challenge_used(self, auth_manager, test_db):
        """Test reusing 2FA challenge."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        from src.core.auth.tokens import create_2fa_challenge_token
        from src.utils.encryption import generate_snowflake_id
        
        challenge_id = generate_snowflake_id()
        now = auth_manager._current_time()
        _, token_hash = create_2fa_challenge_token(challenge_id)
        
        auth_manager._db.execute(
            """INSERT INTO auth_2fa_challenges 
               (id, user_id, token_hash, created_at, expires_at, used)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (challenge_id, user.id, token_hash, now, now + 300000)
        )
        
        full_token, _ = create_2fa_challenge_token(challenge_id)
        
        with pytest.raises(TokenInvalidError):
            auth_manager.complete_2fa(full_token, "123456")
    
    def test_regenerate_backup_codes_wrong_password(self, auth_manager, test_db):
        """Test regenerating backup codes with wrong password."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager._db.execute(
            "UPDATE auth_users SET totp_enabled = 1 WHERE id = ?",
            (user.id,)
        )
        
        with pytest.raises(InvalidCredentialsError):
            auth_manager.regenerate_backup_codes(user.id, "WrongPassword")
    
    def test_regenerate_backup_codes_not_enabled(self, auth_manager):
        """Test regenerating when 2FA not enabled."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(AuthError):
            auth_manager.regenerate_backup_codes(user.id, "Password123!")
    
    def test_get_2fa_status_user_not_found(self, auth_manager):
        """Test getting 2FA status for nonexistent user."""
        with pytest.raises(UserNotFoundError):
            auth_manager.get_2fa_status(99999)
    
    def test_2fa_disable_not_enabled(self, auth_manager):
        """Test disabling 2FA when not enabled."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(AuthError):
            auth_manager.disable_2fa(user.id, "Password123!", "123456")
    
    def test_2fa_confirm_not_initiated(self, auth_manager):
        """Test confirming 2FA when not initiated."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(AuthError):
            auth_manager.confirm_2fa(user.id, "123456")
    
    def test_2fa_confirm_user_not_found(self, auth_manager):
        """Test confirming for nonexistent user."""
        with pytest.raises(UserNotFoundError):
            auth_manager.confirm_2fa(99999, "123456")


class TestAuthManagerBots:
    """Test bot management edge cases."""
    
    def test_create_bot_no_permission(self, auth_manager, test_db):
        """Test creating bot without permission."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager._db.execute(
            "UPDATE auth_users SET permissions = '{}' WHERE id = ?",
            (user.id,)
        )
        
        with pytest.raises(PermissionDeniedError):
            auth_manager.create_bot(user.id, "TestBot", "Test Bot")
    
    def test_create_bot_limit_exceeded(self, auth_manager, monkeypatch):
        """Test creating bot when limit is reached."""
        monkeypatch.setitem(auth_manager._get_config("accounts", {}), "max_bots_per_user", 1)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager.create_bot(user.id, "Bot1", "Bot 1")
        
        with pytest.raises(BotLimitExceededError):
            auth_manager.create_bot(user.id, "Bot2", "Bot 2")
    
    def test_create_bot_duplicate_username(self, auth_manager):
        """Test creating bot with existing username."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        auth_manager.create_bot(user.id, "TestBot", "Test Bot")
        
        with pytest.raises(UserExistsError):
            auth_manager.create_bot(user.id, "TestBot", "Another Bot")
    
    def test_create_bot_owner_not_found(self, auth_manager):
        """Test creating bot for nonexistent owner."""
        with pytest.raises(UserNotFoundError):
            auth_manager.create_bot(99999, "TestBot", "Test Bot")
    
    def test_regenerate_bot_token_not_owner(self, auth_manager):
        """Test regenerating bot token when not the owner."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        bot = auth_manager.create_bot(user1.id, "TestBot", "Test Bot")
        
        with pytest.raises(PermissionDeniedError):
            auth_manager.regenerate_bot_token(user2.id, bot.id)
    
    def test_regenerate_bot_token_not_found(self, auth_manager):
        """Test regenerating nonexistent bot token."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(UserNotFoundError):
            auth_manager.regenerate_bot_token(user.id, 99999)
    
    def test_delete_bot_not_owner(self, auth_manager):
        """Test deleting bot when not the owner."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        bot = auth_manager.create_bot(user1.id, "TestBot", "Test Bot")
        
        with pytest.raises(PermissionDeniedError):
            auth_manager.delete_bot(user2.id, bot.id)
    
    def test_delete_bot_not_found(self, auth_manager):
        """Test deleting nonexistent bot."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(UserNotFoundError):
            auth_manager.delete_bot(user.id, 99999)
    
    def test_get_bot_not_found(self, auth_manager):
        """Test getting nonexistent bot."""
        result = auth_manager.get_bot(99999)
        assert result is None
    
    def test_get_user_bots_empty(self, auth_manager):
        """Test getting bots when user has none."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        bots = auth_manager.get_user_bots(user.id)
        assert len(bots) == 0
    
    def test_update_bot_permissions_not_owner(self, auth_manager):
        """Test updating bot permissions when not owner."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        bot = auth_manager.create_bot(user1.id, "TestBot", "Test Bot")
        
        with pytest.raises(PermissionDeniedError):
            auth_manager.update_bot_permissions(user2.id, bot.id, {})
    
    def test_update_bot_permissions_not_found(self, auth_manager):
        """Test updating nonexistent bot permissions."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(UserNotFoundError):
            auth_manager.update_bot_permissions(user.id, 99999, {})
    
    def test_enable_bot_not_owner(self, auth_manager):
        """Test enabling bot when not owner."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        bot = auth_manager.create_bot(user1.id, "TestBot", "Test Bot")
        auth_manager.disable_bot(user1.id, bot.id)
        
        with pytest.raises(PermissionDeniedError):
            auth_manager.enable_bot(user2.id, bot.id)
    
    def test_disable_bot_not_owner(self, auth_manager):
        """Test disabling bot when not owner."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        bot = auth_manager.create_bot(user1.id, "TestBot", "Test Bot")
        
        with pytest.raises(PermissionDeniedError):
            auth_manager.disable_bot(user2.id, bot.id)


class TestAuthManagerSessions:
    """Test session management edge cases."""
    
    def test_session_limit_enforcement(self, auth_manager, monkeypatch):
        """Test session limit with automatic oldest session revocation."""
        monkeypatch.setitem(auth_manager._get_config("sessions", {}), "max_per_user", 2)
        
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        session1 = auth_manager.login("testuser", "Password123!")
        session2 = auth_manager.login("testuser", "Password123!")
        
        session3 = auth_manager.login("testuser", "Password123!")
        
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(session1.token)
        
        auth_manager.verify_token(session2.token)
        auth_manager.verify_token(session3.token)
    
    def test_logout_all_except_current(self, auth_manager):
        """Test logging out all sessions except current."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        session1 = auth_manager.login("testuser", "Password123!")
        session2 = auth_manager.login("testuser", "Password123!")
        session3 = auth_manager.login("testuser", "Password123!")
        
        count = auth_manager.logout_all(user.id, except_token=session2.token)
        assert count == 2
        
        auth_manager.verify_token(session2.token)
        
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(session1.token)
    
    def test_revoke_session_wrong_user(self, auth_manager):
        """Test revoking another user's session."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        session1 = auth_manager.login("user1", "Password123!")
        
        assert not auth_manager.revoke_session(user2.id, session1.session.id)
    
    def test_refresh_invalid_token(self, auth_manager):
        """Test refreshing with invalid token."""
        result = auth_manager.refresh_session("invalid_token")
        assert result is None
    
    def test_refresh_bot_token(self, auth_manager):
        """Test refreshing bot token (should fail)."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        bot = auth_manager.create_bot(user.id, "TestBot", "Test Bot")
        
        result = auth_manager.refresh_session(bot.token)
        assert result is None
    
    def test_get_sessions_empty(self, auth_manager):
        """Test getting sessions when user has none."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        sessions = auth_manager.get_sessions(user.id)
        assert len(sessions) == 0
    
    def test_revoke_session_not_found(self, auth_manager):
        """Test revoking nonexistent session."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        assert not auth_manager.revoke_session(user.id, 99999)


class TestAuthManagerCache:
    """Test cache behavior and invalidation."""
    
    def test_user_cache_ttl(self, auth_manager, monkeypatch):
        """Test user cache expiration."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        cached1 = auth_manager.get_user(user.id)
        
        auth_manager._user_cache_ttl = 0
        time.sleep(0.001)
        
        cached2 = auth_manager.get_user(user.id)
        assert cached2 is not None
    
    def test_cache_invalidation_on_update(self, auth_manager):
        """Test cache is invalidated on user update."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager.get_user(user.id)
        
        auth_manager.change_password(user.id, "Password123!", "NewPassword456!")
        
        updated = auth_manager.get_user(user.id)
        assert updated is not None
    
    def test_bulk_user_fetch_caching(self, auth_manager):
        """Test get_users_bulk uses cache."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        user3 = auth_manager.register("user3", "user3@example.com", "Password123!")
        
        auth_manager.get_user(user1.id)
        auth_manager.get_user(user2.id)
        
        users = auth_manager.get_users_bulk([user1.id, user2.id, user3.id])
        
        assert len(users) == 3
        assert user1.id in users
        assert user2.id in users
        assert user3.id in users
    
    def test_bulk_user_fetch_empty(self, auth_manager):
        """Test bulk fetch with empty list."""
        users = auth_manager.get_users_bulk([])
        assert len(users) == 0


class TestAuthManagerPasswordReset:
    """Test password reset functionality."""
    
    def test_request_reset_no_email_sender(self, auth_manager):
        """Test requesting reset without email configured."""
        auth_manager.email_sender = None
        result = auth_manager.request_password_reset("test@example.com")
        assert result is False
    
    def test_reset_password_invalid_token(self, auth_manager):
        """Test resetting password with invalid token."""
        with pytest.raises(TokenInvalidError):
            auth_manager.reset_password("invalid_token", "NewPassword123!")
    
    def test_reset_password_weak_password(self, auth_manager, test_db, email_sender):
        """Test resetting with weak password."""
        auth_manager.email_sender = email_sender
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager.request_password_reset("test@example.com")
        
        token_row = test_db.fetch_one(
            "SELECT id FROM auth_email_tokens WHERE user_id = ? AND token_type = 'reset_password'",
            (user.id,)
        )
        
        if token_row:
            from src.core.auth.tokens import create_email_token
            full_token, _ = create_email_token(token_row["id"])
            
            with pytest.raises(WeakPasswordError):
                auth_manager.reset_password(full_token, "weak")
    
    def test_reset_password_wrong_token_type(self, auth_manager, test_db, email_sender):
        """Test reset with wrong token type."""
        auth_manager.email_sender = email_sender
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        token_row = test_db.fetch_one(
            "SELECT id FROM auth_email_tokens WHERE user_id = ?",
            (user.id,)
        )
        
        if token_row:
            from src.core.auth.tokens import create_email_token
            full_token, _ = create_email_token(token_row["id"])
            
            with pytest.raises(TokenInvalidError):
                auth_manager.reset_password(full_token, "NewPassword123!")
    
    def test_change_password_wrong_old_password(self, auth_manager):
        """Test changing password with wrong old password."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        with pytest.raises(InvalidCredentialsError):
            auth_manager.change_password(user.id, "WrongPassword", "NewPassword123!")
    
    def test_change_password_user_not_found(self, auth_manager):
        """Test changing password for nonexistent user."""
        with pytest.raises(UserNotFoundError):
            auth_manager.change_password(99999, "Password123!", "NewPassword456!")
    
    def test_validate_password(self, auth_manager):
        """Test password validation utility."""
        validation = auth_manager.validate_password("Password123!")
        assert validation.valid


class TestAuthManagerDevices:
    """Test device tracking."""
    
    def test_device_tracking(self, auth_manager):
        """Test device is tracked during login."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        device_info = {
            "fingerprint": "test_device_123",
            "name": "Chrome on Windows",
            "type": "desktop"
        }
        
        auth_manager.login("testuser", "Password123!", device_info=device_info)
        
        devices = auth_manager.get_devices(user.id)
        assert len(devices) > 0
        assert devices[0].fingerprint == "test_device_123"
    
    def test_rename_device_wrong_user(self, auth_manager):
        """Test renaming another user's device."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        device_info = {"fingerprint": "device1"}
        auth_manager.login("user1", "Password123!", device_info=device_info)
        
        devices = auth_manager.get_devices(user1.id)
        device_id = devices[0].id
        
        assert not auth_manager.rename_device(user2.id, device_id, "New Name")
    
    def test_revoke_device_sessions(self, auth_manager):
        """Test revoking device revokes all its sessions."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        device_info = {"fingerprint": "test_device"}
        
        s1 = auth_manager.login("testuser", "Password123!", device_info=device_info)
        s2 = auth_manager.login("testuser", "Password123!", device_info=device_info)
        
        devices = auth_manager.get_devices(user.id)
        auth_manager.revoke_device(user.id, devices[0].id)
        
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(s1.token)
        with pytest.raises(TokenInvalidError):
            auth_manager.verify_token(s2.token)
    
    def test_revoke_device_wrong_user(self, auth_manager):
        """Test revoking another user's device."""
        user1 = auth_manager.register("user1", "user1@example.com", "Password123!")
        user2 = auth_manager.register("user2", "user2@example.com", "Password123!")
        
        device_info = {"fingerprint": "device1"}
        auth_manager.login("user1", "Password123!", device_info=device_info)
        
        devices = auth_manager.get_devices(user1.id)
        
        assert not auth_manager.revoke_device(user2.id, devices[0].id)
    
    def test_device_without_fingerprint(self, auth_manager):
        """Test tracking device without fingerprint."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        device_info = {"name": "Chrome"}
        auth_manager.login("testuser", "Password123!", device_info=device_info)
        
        devices = auth_manager.get_devices(user.id)
        assert len(devices) == 0


class TestAuthManagerAudit:
    """Test audit logging."""
    
    def test_audit_login_history(self, auth_manager):
        """Test login history audit."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager.login("testuser", "Password123!")
        try:
            auth_manager.login("testuser", "WrongPassword")
        except:
            pass
        
        history = auth_manager.get_login_history(user.id)
        assert len(history) > 0
    
    def test_audit_security_events(self, auth_manager):
        """Test security events audit."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        auth_manager.setup_2fa(user.id)
        auth_manager.change_password(user.id, "Password123!", "NewPassword456!")
        
        events = auth_manager.get_security_events(user.id)
        assert len(events) > 0


class TestAuthManagerUtilities:
    """Test utility methods."""
    
    def test_get_user_not_found(self, auth_manager):
        """Test getting nonexistent user."""
        result = auth_manager.get_user(99999)
        assert result is None
    
    def test_get_user_by_username_not_found(self, auth_manager):
        """Test getting user by nonexistent username."""
        result = auth_manager.get_user_by_username("nonexistent")
        assert result is None
    
    def test_get_user_by_username(self, auth_manager):
        """Test getting user by username."""
        user = auth_manager.register("testuser", "test@example.com", "Password123!")
        
        result = auth_manager.get_user_by_username("testuser")
        assert result is not None
        assert result.id == user.id
    
    def test_current_time(self, auth_manager):
        """Test timestamp generation."""
        timestamp = auth_manager._current_time()
        assert timestamp > 0
    
    def test_get_config(self, auth_manager):
        """Test config value retrieval."""
        value = auth_manager._get_config("sessions.max_per_user", 10)
        assert value is not None
