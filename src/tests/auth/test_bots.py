"""
Bot account tests for auth module.

Note: Bot tests need fresh users because bots accumulate on users.
Each test creates its own user to avoid hitting the 5 bot limit.
"""

import pytest
import uuid


def unique_name(prefix):
    """Generate a unique username."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def fresh_user(db_and_auth):
    """Create a fresh user for bot tests."""
    db, auth = db_and_auth
    name = unique_name("botuser")
    user = auth.register(name, f"{name}@example.com", "TestPass123!")
    return user, auth


class TestBots:
    """Test bot account management."""

    def test_create_bot(self, fresh_user):
        """Test creating a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(
            owner_id=user.id,
            username=unique_name("bot"),
            display_name="Test Bot"
        )

        assert bot is not None
        assert bot.owner_id == user.id
        assert bot.token is not None

    def test_create_bot_with_permissions(self, fresh_user):
        """Test creating bot with custom permissions."""
        user, auth = fresh_user

        bot = auth.create_bot(
            owner_id=user.id,
            username=unique_name("permbot"),
            display_name="Permission Bot",
            permissions={"messages.send": True, "messages.read": True}
        )

        assert bot.permissions.get("messages.send") is True

    def test_create_bot_restricted_permission_fails(self, fresh_user):
        """Test creating bot with restricted permission fails."""
        user, auth = fresh_user

        with pytest.raises(auth.PermissionDeniedError):
            auth.create_bot(
                owner_id=user.id,
                username=unique_name("badbot"),
                display_name="Bad Bot",
                permissions={"bots.create": True}
            )

    def test_create_bot_duplicate_username_fails(self, fresh_user):
        """Test creating bot with duplicate username fails."""
        user, auth = fresh_user

        bot_name = unique_name("uniquebot")
        auth.create_bot(user.id, bot_name, "Unique Bot")

        with pytest.raises(auth.UserExistsError):
            auth.create_bot(user.id, bot_name, "Another Bot")

    def test_create_bot_respects_limit(self, fresh_user):
        """Test bot creation respects limit."""
        user, auth = fresh_user

        for i in range(5):
            auth.create_bot(user.id, unique_name(f"limitbot{i}"), f"Bot {i}")

        with pytest.raises(auth.BotLimitExceededError):
            auth.create_bot(user.id, unique_name("limitbot6"), "Bot 6")

    def test_bot_token_format(self, fresh_user):
        """Test bot token has correct format."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("formatbot"), "Format Bot")

        assert bot.token.startswith("bot.")
        parts = bot.token.split(".")
        assert len(parts) == 3

    def test_verify_bot_token(self, fresh_user):
        """Test verifying bot token."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("verifybot"), "Verify Bot")

        token_info = auth.verify_token(bot.token)

        assert token_info.valid is True
        assert token_info.token_type == "bot"
        assert token_info.account_id == bot.id
        assert token_info.user_id == user.id

    def test_regenerate_bot_token(self, fresh_user):
        """Test regenerating bot token."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("regenbot"), "Regen Bot")
        old_token = bot.token

        new_token = auth.regenerate_bot_token(user.id, bot.id)

        assert new_token != old_token
        assert new_token.startswith("bot.")

    def test_regenerate_bot_token_invalidates_old(self, fresh_user):
        """Test regenerating bot token invalidates old one."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("invalidbot"), "Invalid Bot")
        old_token = bot.token

        auth.regenerate_bot_token(user.id, bot.id)

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(old_token)

    def test_regenerate_bot_token_wrong_owner(self, db_and_auth):
        """Test regenerating bot token by non-owner fails."""
        db, auth = db_and_auth

        name1 = unique_name("botowner")
        name2 = unique_name("notowner")
        user1 = auth.register(name1, f"{name1}@example.com", "TestPass123!")
        user2 = auth.register(name2, f"{name2}@example.com", "TestPass123!")

        bot = auth.create_bot(user1.id, unique_name("ownedbot"), "Owned Bot")

        with pytest.raises(auth.PermissionDeniedError):
            auth.regenerate_bot_token(user2.id, bot.id)

    def test_get_bot(self, fresh_user):
        """Test getting bot by ID."""
        user, auth = fresh_user

        created = auth.create_bot(user.id, unique_name("getbot"), "Get Bot")

        bot = auth.get_bot(created.id)

        assert bot is not None
        assert bot.id == created.id

    def test_get_user_bots(self, fresh_user):
        """Test getting all bots for a user."""
        user, auth = fresh_user

        auth.create_bot(user.id, unique_name("listbot1"), "List Bot 1")
        auth.create_bot(user.id, unique_name("listbot2"), "List Bot 2")

        bots = auth.get_user_bots(user.id)

        assert len(bots) >= 2

    def test_disable_bot(self, fresh_user):
        """Test disabling a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("disablebot"), "Disable Bot")

        result = auth.disable_bot(user.id, bot.id)
        assert result is True

        with pytest.raises(auth.TokenInvalidError):
            auth.verify_token(bot.token)

    def test_enable_bot(self, fresh_user):
        """Test re-enabling a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("enablebot"), "Enable Bot")
        token = bot.token

        auth.disable_bot(user.id, bot.id)
        auth.enable_bot(user.id, bot.id)

        token_info = auth.verify_token(token)
        assert token_info.valid is True

    def test_delete_bot(self, fresh_user):
        """Test deleting a bot."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("deletebot"), "Delete Bot")

        result = auth.delete_bot(user.id, bot.id)
        assert result is True

        assert auth.get_bot(bot.id) is None

    def test_bot_cannot_create_bots(self, fresh_user):
        """Test that bots cannot create other bots."""
        user, auth = fresh_user

        bot = auth.create_bot(user.id, unique_name("parentbot"), "Parent Bot")

        token_info = auth.verify_token(bot.token)

        assert not auth.has_capability(token_info, "bots.create")
