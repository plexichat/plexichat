"""
Bot account tests for auth module.

Note: Bot tests need fresh users because bots accumulate on users.
Each test creates its own user to avoid hitting the 5 bot limit.
"""

import pytest
import uuid
from src.core.auth.permissions import permissions_to_json
from src.core.auth.exceptions import (
    AuthError,
    UserExistsError,
    PermissionDeniedError,
    BotLimitExceededError,
    TokenInvalidError,
)
from unittest.mock import patch


@pytest.fixture
def fresh_user(db, auth_manager):
    """Create a fresh user for bot tests."""
    from src.utils import encryption

    name = f"testuser_{uuid.uuid4().hex[:8]}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(name, f"{name}@example.com", "TestPass123!")
    return user, auth_manager


class TestBots:
    """Test bot account management."""

    def test_create_bot(self, fresh_user):
        """Test creating a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(
            owner_id=user.id, username="bot_test1", display_name="Test Bot"
        )

        assert bot is not None
        assert bot.owner_id == user.id
        assert bot.token is not None

    def test_create_bot_with_permissions(self, fresh_user):
        """Test creating bot with custom permissions."""
        user, auth = fresh_user

        bot = auth.create_bot(
            owner_id=user.id,
            username="permbot_test1",
            display_name="Permission Bot",
            permissions={"messages.send": True, "messages.read": True},
        )

        assert bot.permissions.get("messages.send") is True

    def test_create_bot_restricted_permission_fails(self, fresh_user):
        """Test creating bot with restricted permission fails."""
        user, auth = fresh_user

        with pytest.raises(AuthError, match="Bots cannot have permission"):
            auth.create_bot(
                owner_id=user.id,
                username="badbot_test1",
                display_name="Bad Bot",
                permissions={"bots.create": True},
            )

    def test_create_bot_cannot_escalate_owner_permissions(self, db, auth_manager):
        """Test bot creation cannot grant permissions the owner does not hold."""
        from src.utils import encryption

        name = "limiteduser_test1"
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(name, f"{name}@example.com", "TestPass123!")

        db.execute(
            "UPDATE auth_users SET permissions = ? WHERE id = ?",
            (
                permissions_to_json(
                    {
                        "bots.create": True,
                        "messages.read": True,
                    }
                ),
                user.id,
            ),
        )

        with pytest.raises(PermissionDeniedError, match="Cannot grant bot permission"):
            auth_manager.create_bot(
                owner_id=user.id,
                username="escalatebot_test1",
                display_name="Escalate Bot",
                permissions={"messages.read": True, "messages.delete_others": True},
            )

    def test_create_bot_duplicate_username_fails(self, fresh_user):
        """Test creating bot with duplicate username fails."""
        user, auth = fresh_user

        bot_name = "uniquebot_test1"
        auth.create_bot(user.id, bot_name, "Unique Bot")

        with pytest.raises(UserExistsError):
            auth.create_bot(user.id, bot_name, "Another Bot")

    def test_create_bot_respects_limit(self, fresh_user):
        """Test bot creation respects limit."""
        user, auth = fresh_user

        for i in range(5):
            auth.create_bot(user.id, f"limitbot{i}_test1", f"Bot {i}")

        with pytest.raises(BotLimitExceededError):
            auth.create_bot(user.id, "limitbot6_test1", "Bot 6")

    def test_bot_token_format(self, fresh_user):
        """Test bot token has correct format."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "formatbot_test1", "Format Bot")

        assert bot.token.startswith("bot.")
        parts = bot.token.split(".")
        assert len(parts) == 3

    def test_verify_bot_token(self, fresh_user):
        """Test verifying bot token."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "verifybot_test1", "Verify Bot")

        token_info = auth.verify_token(bot.token)

        assert token_info.valid is True
        assert token_info.token_type == "bot"
        assert token_info.account_id == bot.id
        assert token_info.user_id == user.id

    def test_regenerate_bot_token(self, fresh_user):
        """Test regenerating bot token."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "regenbot_test1", "Regen Bot")
        old_token = bot.token

        new_token = auth.regenerate_bot_token(user.id, bot.id)

        assert new_token != old_token
        assert new_token.startswith("bot.")

    def test_regenerate_bot_token_invalidates_old(self, fresh_user):
        """Test regenerating bot token invalidates old one."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "invalidbot_test1", "Invalid Bot")
        old_token = bot.token

        auth.regenerate_bot_token(user.id, bot.id)

        with pytest.raises(TokenInvalidError):
            auth.verify_token(old_token)

    def test_regenerate_bot_token_wrong_owner(self, db, auth_manager):
        """Test regenerating bot token by non-owner fails."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "usera_test1", "usera_test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "userb_test1", "userb_test1@example.com", "TestPass123!"
            )

        bot = auth_manager.create_bot(user1.id, "ownedbot_test1", "Owned Bot")

        with pytest.raises(PermissionDeniedError):
            auth_manager.regenerate_bot_token(user2.id, bot.id)

    def test_get_bot(self, fresh_user):
        """Test getting bot by ID."""
        user, auth = fresh_user

        created = auth.create_bot(user.id, "getbot_test1", "Get Bot")

        bot = auth.get_bot(created.id)

        assert bot is not None
        assert bot.id == created.id

    def test_get_user_bots(self, fresh_user):
        """Test getting all bots for a user."""
        user, auth = fresh_user

        auth.create_bot(user.id, "listbot1_test1", "List Bot 1")
        auth.create_bot(user.id, "listbot2_test1", "List Bot 2")

        bots = auth.get_user_bots(user.id)

        assert len(bots) >= 2

    def test_disable_bot(self, fresh_user):
        """Test disabling a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "disablebot_test1", "Disable Bot")

        result = auth.disable_bot(user.id, bot.id)
        assert result is True

        with pytest.raises(TokenInvalidError):
            auth.verify_token(bot.token)

    def test_enable_bot(self, fresh_user):
        """Test re-enabling a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "enablebot_test1", "Enable Bot")
        token = bot.token

        auth.disable_bot(user.id, bot.id)
        auth.enable_bot(user.id, bot.id)

        token_info = auth.verify_token(token)
        assert token_info.valid is True

    def test_delete_bot(self, fresh_user):
        """Test deleting a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "deletebot_test1", "Delete Bot")

        result = auth.delete_bot(user.id, bot.id)
        assert result is True

        assert auth.get_bot(bot.id) is None

    def test_bot_cannot_create_bots(self, fresh_user):
        """Test that bots cannot create other bots."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, "parentbot_test1", "Parent Bot")

        token_info = auth.verify_token(bot.token)

        assert not auth.has_capability(token_info, "bots.create")
