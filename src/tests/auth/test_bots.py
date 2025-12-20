"""
Comprehensive bot tests covering token leakage, permissions, and security.
"""

import pytest
import time
from src.core.auth.exceptions import (
    UserNotFoundError,
    PermissionDeniedError,
    InvalidUsernameError,
    UserExistsError,
    BotLimitExceededError,
    TokenInvalidError,
)
from src.tests.fixtures.config import TEST_PASSWORD


class TestBotCreation:
    """Tests for creating bot accounts."""

    def test_create_bot_success(self, modules):
        """Test successful bot creation."""
        username = f"owner_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Test Bot", None)

        assert bot.id is not None
        assert bot.owner_id == user.id
        assert bot.token is not None

    def test_create_bot_returns_token_once(self, modules):
        """Test bot token is only returned on creation."""
        username = f"tokenonce_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Test Bot")
        token = bot.token

        # Retrieve bot - should not have token
        retrieved = modules.auth.get_bot(bot.id)
        assert retrieved.token is None
        assert token is not None

    def test_create_bot_has_default_permissions(self, modules):
        """Test bot has default permissions when none specified."""
        username = f"defaultperms_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Test Bot")

        assert bot.permissions is not None
        assert bot.permissions.get("messages.send") is True
        assert bot.permissions.get("bots.create") is False  # Restricted

    def test_create_bot_custom_permissions(self, modules):
        """Test bot with custom permissions."""
        username = f"customperms_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        perms = {"messages.send": True, "messages.read": True}
        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Test Bot", perms)

        assert bot.permissions == perms

    def test_create_bot_invalid_username(self, modules):
        """Test bot creation with invalid username."""
        username = f"owner_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        with pytest.raises(InvalidUsernameError):
            modules.auth.create_bot(user.id, "invalid username", "Test Bot")

    def test_create_bot_duplicate_username(self, modules):
        """Test cannot create bot with duplicate username."""
        username = f"owner_{time.time()}"
        bot_username = f"bot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.create_bot(user.id, bot_username, "Bot 1")

        with pytest.raises(UserExistsError):
            modules.auth.create_bot(user.id, bot_username, "Bot 2")

    def test_create_bot_username_conflicts_with_user(self, modules):
        """Test bot username cannot conflict with user username."""
        username = f"conflict_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        with pytest.raises(UserExistsError):
            modules.auth.create_bot(user.id, username, "Bot")

    def test_create_bot_limit_enforced(self, modules):
        """Test bot creation limit is enforced."""
        username = f"botlimit_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create max bots (5 in config)
        for i in range(5):
            modules.auth.create_bot(user.id, f"bot{i}_{time.time()}", f"Bot {i}")

        # 6th should fail
        with pytest.raises(BotLimitExceededError):
            modules.auth.create_bot(user.id, f"bot6_{time.time()}", "Bot 6")

    def test_create_bot_by_nonexistent_owner(self, modules):
        """Test creating bot with non-existent owner fails."""
        with pytest.raises(UserNotFoundError):
            modules.auth.create_bot(999999999, "botname", "Bot")


class TestBotPermissions:
    """Tests for bot permission restrictions."""

    def test_bot_cannot_create_bots(self, modules):
        """Test bots cannot have bots.create permission."""
        username = f"nobotcreate_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        perms = {"bots.create": True}

        with pytest.raises(PermissionDeniedError):
            modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot", perms)

    def test_bot_cannot_have_admin_permissions(self, modules):
        """Test bots cannot have admin permissions."""
        username = f"noadmin_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        perms = {"admin.system": True}

        with pytest.raises(PermissionDeniedError):
            modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot", perms)

    def test_bot_cannot_delete_account(self, modules):
        """Test bots cannot have account.delete permission."""
        username = f"nodelete_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        perms = {"account.delete": True}

        with pytest.raises(PermissionDeniedError):
            modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot", perms)

    def test_bot_valid_permissions_accepted(self, modules):
        """Test bots can have valid permissions."""
        username = f"validperms_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        perms = {
            "messages.send": True,
            "messages.read": True,
            "conversations.join": True,
        }

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot", perms)
        assert bot.permissions == perms

    def test_update_bot_permissions(self, modules):
        """Test updating bot permissions."""
        username = f"updateperms_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        new_perms = {"messages.send": False, "messages.read": True}
        updated = modules.auth.update_bot_permissions(user.id, bot.id, new_perms)

        assert updated.permissions == new_perms

    def test_update_bot_permissions_validates_restrictions(self, modules):
        """Test updating bot permissions still validates restrictions."""
        username = f"updaterestrict_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        with pytest.raises(PermissionDeniedError):
            modules.auth.update_bot_permissions(user.id, bot.id, {"admin.system": True})


class TestBotTokens:
    """Tests for bot token security."""

    def test_bot_token_format(self, modules):
        """Test bot token has correct format."""
        username = f"tokenformat_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        assert bot.token.startswith("bot.")
        parts = bot.token.split(".")
        assert len(parts) == 3  # bot.id.secret

    def test_bot_token_verify(self, modules):
        """Test verifying bot token."""
        username = f"verifybot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        token_info = modules.auth.verify_token(bot.token)
        assert token_info.valid is True
        assert token_info.token_type == "bot"
        assert token_info.account_id == bot.id

    def test_bot_token_randomness(self, modules):
        """Test bot tokens are randomly generated."""
        username = f"random_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        tokens = []
        for i in range(5):
            bot = modules.auth.create_bot(user.id, f"bot{i}_{time.time()}", f"Bot {i}")
            tokens.append(bot.token)

        # All should be unique
        assert len(tokens) == len(set(tokens))

    def test_bot_token_sufficient_entropy(self, modules):
        """Test bot token has sufficient entropy."""
        username = f"entropy_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        secret = bot.token.split(".")[2]

        # Should be at least 48 bytes base64 encoded (config)
        assert len(secret) >= 60

    def test_regenerate_bot_token(self, modules):
        """Test regenerating bot token."""
        username = f"regen_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        old_token = bot.token

        new_token = modules.auth.regenerate_bot_token(user.id, bot.id)

        assert new_token != old_token
        assert new_token.startswith("bot.")

        # Old token should not work
        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(old_token)

        # New token should work
        token_info = modules.auth.verify_token(new_token)
        assert token_info.valid is True

    def test_regenerate_bot_token_wrong_owner(self, modules):
        """Test cannot regenerate token for bot owned by another user."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        u1 = modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(u1.id, f"bot_{time.time()}", "Bot")

        with pytest.raises(PermissionDeniedError):
            modules.auth.regenerate_bot_token(u2.id, bot.id)


class TestBotTokenLeakage:
    """Tests to prevent bot token leakage."""

    def test_bot_token_not_in_get_bot(self, modules):
        """Test get_bot does not return token."""
        username = f"noleak1_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        retrieved = modules.auth.get_bot(bot.id)

        assert retrieved.token is None

    def test_bot_token_not_in_list_bots(self, modules):
        """Test listing bots does not return tokens."""
        username = f"noleak2_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        bots = modules.auth.get_user_bots(user.id)

        for b in bots:
            assert b.token is None

    def test_bot_hash_stored_not_token(self, modules):
        """Test only token hash is stored in database."""
        username = f"hashonly_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        row = modules._db.fetch_one(
            "SELECT token_hash FROM auth_bots WHERE id = ?", (bot.id,)
        )
        assert row["token_hash"] != bot.token
        assert len(row["token_hash"]) == 64  # SHA-256 hex


class TestBotManagement:
    """Tests for bot management operations."""

    def test_get_bot(self, modules):
        """Test retrieving a bot."""
        username = f"getbot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Test Bot")
        retrieved = modules.auth.get_bot(bot.id)

        assert retrieved is not None
        assert retrieved.id == bot.id
        assert retrieved.username == bot.username

    def test_get_nonexistent_bot(self, modules):
        """Test getting non-existent bot returns None."""
        result = modules.auth.get_bot(999999999)
        assert result is None

    def test_get_user_bots(self, modules):
        """Test listing user's bots."""
        username = f"listbots_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Create 3 bots
        for i in range(3):
            modules.auth.create_bot(user.id, f"bot{i}_{time.time()}", f"Bot {i}")

        bots = modules.auth.get_user_bots(user.id)
        assert len(bots) == 3

    def test_get_user_bots_empty(self, modules):
        """Test listing bots for user with no bots."""
        username = f"nobots_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bots = modules.auth.get_user_bots(user.id)
        assert bots == []

    def test_disable_bot(self, modules):
        """Test disabling a bot."""
        username = f"disable_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        result = modules.auth.disable_bot(user.id, bot.id)

        assert result is True

        retrieved = modules.auth.get_bot(bot.id)
        assert retrieved.disabled is True

    def test_disabled_bot_token_fails(self, modules):
        """Test disabled bot's token doesn't work."""
        username = f"disabletoken_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        token = bot.token

        modules.auth.disable_bot(user.id, bot.id)

        with pytest.raises(TokenInvalidError):
            modules.auth.verify_token(token)

    def test_enable_bot(self, modules):
        """Test re-enabling a disabled bot."""
        username = f"enable_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        modules.auth.disable_bot(user.id, bot.id)

        result = modules.auth.enable_bot(user.id, bot.id)
        assert result is True

        retrieved = modules.auth.get_bot(bot.id)
        assert retrieved.disabled is False

    def test_delete_bot(self, modules):
        """Test deleting a bot."""
        username = f"deletebot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        result = modules.auth.delete_bot(user.id, bot.id)

        assert result is True

        retrieved = modules.auth.get_bot(bot.id)
        assert retrieved is None

    def test_delete_bot_wrong_owner(self, modules):
        """Test cannot delete bot owned by another user."""
        user1 = f"user1_{time.time()}"
        user2 = f"user2_{time.time()}"

        u1 = modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(u1.id, f"bot_{time.time()}", "Bot")

        with pytest.raises(PermissionDeniedError):
            modules.auth.delete_bot(u2.id, bot.id)


class TestBotAudit:
    """Tests for bot audit logging."""

    def test_bot_creation_audited(self, modules):
        """Test bot creation creates audit log."""
        username = f"auditcreate_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        events = modules.auth.get_security_events(user.id, limit=10)
        bot_events = [e for e in events if e.event_type.value == "bot_created"]
        assert len(bot_events) > 0

    def test_bot_deletion_audited(self, modules):
        """Test bot deletion creates audit log."""
        username = f"auditdelete_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        modules.auth.delete_bot(user.id, bot.id)

        events = modules.auth.get_security_events(user.id, limit=10)
        delete_events = [e for e in events if e.event_type.value == "bot_deleted"]
        assert len(delete_events) > 0

    def test_bot_token_regen_audited(self, modules):
        """Test bot token regeneration creates audit log."""
        username = f"auditregen_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        modules.auth.regenerate_bot_token(user.id, bot.id)

        events = modules.auth.get_security_events(user.id, limit=10)
        regen_events = [
            e for e in events if e.event_type.value == "bot_token_regenerated"
        ]
        assert len(regen_events) > 0


class TestBotOwnership:
    """Tests for bot ownership validation."""

    def test_only_owner_can_manage_bot(self, modules):
        """Test only bot owner can manage the bot."""
        user1 = f"owner_{time.time()}"
        user2 = f"other_{time.time()}"

        u1 = modules.auth.register(user1, f"{user1}@test.com", TEST_PASSWORD)
        u2 = modules.auth.register(user2, f"{user2}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(u1.id, f"bot_{time.time()}", "Bot")

        # User 2 cannot disable
        with pytest.raises(PermissionDeniedError):
            modules.auth.disable_bot(u2.id, bot.id)

        # User 2 cannot enable
        with pytest.raises(PermissionDeniedError):
            modules.auth.enable_bot(u2.id, bot.id)

        # User 2 cannot update permissions
        with pytest.raises(PermissionDeniedError):
            modules.auth.update_bot_permissions(u2.id, bot.id, {"messages.send": True})

    def test_bot_owner_in_token_info(self, modules):
        """Test token info includes owner user_id."""
        username = f"ownerinfo_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        token_info = modules.auth.verify_token(bot.token)

        assert token_info.user_id == user.id  # Owner
        assert token_info.account_id == bot.id  # Bot itself
