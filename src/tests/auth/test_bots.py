"""
Bot account tests for auth module.
"""

import pytest


class TestBots:
    """Test bot account management."""
    
    def test_create_bot(self, registered_user):
        """Test creating a bot."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(
            owner_id=user.id,
            username=f"bot_{user.id}",
            display_name="Test Bot"
        )
        
        assert bot is not None
        assert bot.owner_id == user.id
        assert bot.token is not None
    
    def test_create_bot_with_permissions(self, registered_user):
        """Test creating bot with custom permissions."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(
            owner_id=user.id,
            username=f"permbot_{user.id}",
            display_name="Permission Bot",
            permissions={"messages.send": True, "messages.read": True}
        )
        
        assert bot.permissions.get("messages.send") is True
    
    def test_create_bot_restricted_permission_fails(self, registered_user):
        """Test creating bot with restricted permission fails."""
        user, auth, username = registered_user
        
        with pytest.raises(auth.PermissionDeniedError):
            auth.create_bot(
                owner_id=user.id,
                username=f"badbot_{user.id}",
                display_name="Bad Bot",
                permissions={"bots.create": True}
            )
    
    def test_create_bot_duplicate_username_fails(self, db_and_auth):
        """Test creating bot with duplicate username fails."""
        db, auth = db_and_auth
        
        user = auth.register("botowner1", "botowner1@example.com", "TestPass123!")
        auth.create_bot(user.id, "uniquebot", "Unique Bot")
        
        with pytest.raises(auth.UserExistsError):
            auth.create_bot(user.id, "uniquebot", "Another Bot")
    
    def test_create_bot_respects_limit(self, db_and_auth):
        """Test bot creation respects limit."""
        db, auth = db_and_auth
        
        user = auth.register("botlimit", "botlimit@example.com", "TestPass123!")
        
        for i in range(5):
            auth.create_bot(user.id, f"limitbot_{i}", f"Bot {i}")
        
        with pytest.raises(auth.BotLimitExceededError):
            auth.create_bot(user.id, "limitbot_6", "Bot 6")
    
    def test_bot_token_format(self, registered_user):
        """Test bot token has correct format."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"formatbot_{user.id}", "Format Bot")
        
        assert bot.token.startswith("bot.")
        parts = bot.token.split(".")
        assert len(parts) == 3
    
    def test_verify_bot_token(self, registered_user):
        """Test verifying bot token."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"verifybot_{user.id}", "Verify Bot")
        
        token_info = auth.verify_token(bot.token)
        
        assert token_info.valid is True
        assert token_info.token_type == "bot"
        assert token_info.account_id == bot.id
        assert token_info.user_id == user.id
    
    def test_regenerate_bot_token(self, registered_user):
        """Test regenerating bot token."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"regenbot_{user.id}", "Regen Bot")
        old_token = bot.token
        
        new_token = auth.regenerate_bot_token(user.id, bot.id)
        
        assert new_token != old_token
        assert new_token.startswith("bot.")
    
    def test_regenerate_bot_token_invalidates_old(self, registered_user):
        """Test regenerating bot token invalidates old one."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"invalidbot_{user.id}", "Invalid Bot")
        old_token = bot.token
        
        auth.regenerate_bot_token(user.id, bot.id)
        
        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(old_token)
    
    def test_regenerate_bot_token_wrong_owner(self, db_and_auth):
        """Test regenerating bot token by non-owner fails."""
        db, auth = db_and_auth
        
        user1 = auth.register("botowner2", "botowner2@example.com", "TestPass123!")
        user2 = auth.register("notowner", "notowner@example.com", "TestPass123!")
        
        bot = auth.create_bot(user1.id, "ownedbot", "Owned Bot")
        
        with pytest.raises(auth.PermissionDeniedError):
            auth.regenerate_bot_token(user2.id, bot.id)
    
    def test_get_bot(self, registered_user):
        """Test getting bot by ID."""
        user, auth, username = registered_user
        
        created = auth.create_bot(user.id, f"getbot_{user.id}", "Get Bot")
        
        bot = auth.get_bot(created.id)
        
        assert bot is not None
        assert bot.id == created.id
    
    def test_get_user_bots(self, registered_user):
        """Test getting all bots for a user."""
        user, auth, username = registered_user
        
        auth.create_bot(user.id, f"listbot1_{user.id}", "List Bot 1")
        auth.create_bot(user.id, f"listbot2_{user.id}", "List Bot 2")
        
        bots = auth.get_user_bots(user.id)
        
        assert len(bots) >= 2
    
    def test_disable_bot(self, registered_user):
        """Test disabling a bot."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"disablebot_{user.id}", "Disable Bot")
        
        result = auth.disable_bot(user.id, bot.id)
        assert result is True
        
        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(bot.token)
    
    def test_enable_bot(self, registered_user):
        """Test re-enabling a bot."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"enablebot_{user.id}", "Enable Bot")
        token = bot.token
        
        auth.disable_bot(user.id, bot.id)
        auth.enable_bot(user.id, bot.id)
        
        token_info = auth.verify_token(token)
        assert token_info.valid is True
    
    def test_delete_bot(self, registered_user):
        """Test deleting a bot."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"deletebot_{user.id}", "Delete Bot")
        
        result = auth.delete_bot(user.id, bot.id)
        assert result is True
        
        assert auth.get_bot(bot.id) is None
    
    def test_bot_cannot_create_bots(self, registered_user):
        """Test that bots cannot create other bots."""
        user, auth, username = registered_user
        
        bot = auth.create_bot(user.id, f"parentbot_{user.id}", "Parent Bot")
        
        token_info = auth.verify_token(bot.token)
        
        assert not auth.has_capability(token_info, "bots.create")
