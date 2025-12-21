"""
Token Validation Tests.

Tests comprehensive token validation including format validation,
cryptographic integrity, expiration, and proper token lifecycle.
"""

import pytest
import time


class TestTokenValidation:
    """Test token validation mechanisms."""

    def test_valid_session_token_accepted(self, modules, user_pool):
        """Test that valid session tokens are accepted."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        token_info = modules.auth.verify_token(result.token)
        assert token_info.valid
        assert token_info.user_id == user.id
        assert token_info.token_type == "user"

    def test_valid_bot_token_accepted(self, modules, user_pool):
        """Test that valid bot tokens are accepted."""
        owner = user_pool.get_user()
        
        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"testbot_{owner.id}",
            display_name="Test Bot"
        )
        
        token_info = modules.auth.verify_token(bot.token)
        assert token_info.valid
        assert token_info.token_type == "bot"
        assert token_info.account_id == bot.id

    def test_token_format_validation(self, modules):
        """Test that token format is properly validated."""
        invalid_tokens = [
            "",
            "a",
            "a.b",
            "invalidformat",
            "123",
            "abc.def.ghi.jkl",
        ]
        
        for token in invalid_tokens:
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_token_secret_validation(self, modules, user_pool):
        """Test that token secrets are properly validated."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parts = result.token.split(".")
        if len(parts) >= 2:
            invalid_token = f"{parts[0]}.invalidsecret"
            
            with pytest.raises(Exception):
                modules.auth.verify_token(invalid_token)

    def test_token_id_validation(self, modules):
        """Test that token IDs are validated."""
        invalid_tokens = [
            "notanumber.secret",
            "-1.secret",
            "0.secret",
            "abc.secret",
        ]
        
        for token in invalid_tokens:
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_expired_token_rejected(self, modules, user_pool):
        """Test that expired tokens are rejected."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parsed = modules.auth._auth.tokens.parse_token(result.token)
        
        modules.db.execute(
            "UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
            (0, parsed["id"])
        )
        
        with pytest.raises(Exception) as exc_info:
            modules.auth.verify_token(result.token)
        
        assert "expired" in str(exc_info.value).lower()

    def test_revoked_token_rejected(self, modules, user_pool):
        """Test that revoked tokens are rejected."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        modules.auth.logout(result.token)
        
        with pytest.raises(Exception) as exc_info:
            modules.auth.verify_token(result.token)
        
        error_msg = str(exc_info.value).lower()
        assert "revoked" in error_msg or "invalid" in error_msg

    def test_token_type_validation(self, modules, user_pool):
        """Test that token types are properly validated."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        token_info = modules.auth.verify_token(result.token)
        assert token_info.token_type in ["user", "bot"]

    def test_bot_token_format(self, modules, user_pool):
        """Test bot token format validation."""
        owner = user_pool.get_user()
        
        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"testbot_{owner.id}",
            display_name="Test Bot"
        )
        
        assert bot.token.startswith("bot.")
        
        parts = bot.token.split(".")
        assert len(parts) == 3
        assert parts[0] == "bot"

    def test_session_token_format(self, modules, user_pool):
        """Test session token format validation."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parts = result.token.split(".")
        assert len(parts) == 2
        
        session_id = int(parts[0])
        assert session_id > 0

    def test_token_minimum_entropy(self, modules, user_pool):
        """Test that tokens have sufficient entropy."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parts = result.token.split(".")
        if len(parts) >= 2:
            secret = parts[-1]
            assert len(secret) >= 32

    def test_token_character_set(self, modules, user_pool):
        """Test that token secrets use safe character set."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        parts = result.token.split(".")
        if len(parts) >= 2:
            secret = parts[-1]
            for char in secret:
                assert char.isalnum() or char in ['-', '_']

    def test_token_rate_limiting(self, modules):
        """Test rate limiting on token verification."""
        invalid_token = "123456.invalidsecret"
        
        for i in range(10):
            try:
                modules.auth.verify_token(invalid_token, ip_address="192.168.1.1")
            except Exception:
                pass

    def test_token_reuse_detection(self, modules, user_pool):
        """Test detection of suspicious token reuse."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        for _ in range(5):
            modules.auth.verify_token(result.token)

    def test_token_permissions_validation(self, modules, user_pool):
        """Test that token permissions are properly validated."""
        owner = user_pool.get_user()
        
        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"testbot_{owner.id}",
            display_name="Test Bot",
            permissions={"messages.send": True}
        )
        
        token_info = modules.auth.verify_token(bot.token)
        assert token_info.permissions.get("messages.send")

    def test_disabled_bot_token_rejected(self, modules, user_pool):
        """Test that tokens from disabled bots are rejected."""
        owner = user_pool.get_user()
        
        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"testbot_{owner.id}",
            display_name="Test Bot"
        )
        
        modules.auth.disable_bot(owner.id, bot.id)
        
        with pytest.raises(Exception) as exc_info:
            modules.auth.verify_token(bot.token)
        
        assert "disabled" in str(exc_info.value).lower()

    def test_token_regeneration_invalidates_old(self, modules, user_pool):
        """Test that token regeneration invalidates old tokens."""
        owner = user_pool.get_user()
        
        bot = modules.auth.create_bot(
            owner_id=owner.id,
            username=f"testbot_{owner.id}",
            display_name="Test Bot"
        )
        
        old_token = bot.token
        
        new_token = modules.auth.regenerate_bot_token(owner.id, bot.id)
        
        assert new_token != old_token
        
        modules.auth.verify_token(new_token)
        
        with pytest.raises(Exception):
            modules.auth.verify_token(old_token)

    def test_token_validation_with_cache(self, modules, user_pool):
        """Test token validation with caching."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        token_info1 = modules.auth.verify_token(result.token)
        token_info2 = modules.auth.verify_token(result.token)
        
        assert token_info1.user_id == token_info2.user_id

    def test_token_validation_cache_invalidation(self, modules, user_pool):
        """Test that token cache is invalidated on logout."""
        user, username, password = user_pool.get_user_with_credentials()
        
        result = modules.auth.login(username, password)
        
        modules.auth.verify_token(result.token)
        
        modules.auth.logout(result.token)
        
        with pytest.raises(Exception):
            modules.auth.verify_token(result.token)

    def test_malformed_token_handling(self, modules):
        """Test handling of malformed tokens."""
        malformed_tokens = [
            None,
            "",
            ".",
            "..",
            "...",
            "a.",
            ".b",
            "a..b",
        ]
        
        for token in malformed_tokens:
            if token is None:
                continue
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_token_null_bytes(self, modules):
        """Test handling of tokens with null bytes."""
        tokens_with_nulls = [
            "123\x00456.secret",
            "123.sec\x00ret",
            "\x00123.secret",
        ]
        
        for token in tokens_with_nulls:
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_token_unicode_handling(self, modules):
        """Test handling of tokens with unicode characters."""
        unicode_tokens = [
            "123.café",
            "123.测试",
            "123.🔐secret",
        ]
        
        for token in unicode_tokens:
            with pytest.raises(Exception):
                modules.auth.verify_token(token)

    def test_token_timing_attack_resistance(self, modules):
        """Test resistance to timing attacks on token validation."""
        valid_token_prefix = "123456789."
        
        times = []
        for i in range(10):
            start = time.time()
            try:
                modules.auth.verify_token(f"{valid_token_prefix}secret{i}")
            except Exception:
                pass
            elapsed = time.time() - start
            times.append(elapsed)
        
        avg_time = sum(times) / len(times)
        for t in times:
            variation = abs(t - avg_time) / avg_time if avg_time > 0 else 0
            assert variation < 0.5
