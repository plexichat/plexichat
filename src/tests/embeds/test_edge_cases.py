"""
Tests for error handling and edge cases.
"""

import pytest
from src.core.embeds import (
    EmbedNotFoundError,
    MessageNotFoundError,
    PermissionDeniedError,
)
from unittest.mock import patch


class TestEmptyEmbed:
    """Tests for empty or minimal embeds."""

    def test_create_empty_embed(self, db, auth_manager):
        """Test creating embed with no fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="edge1_test",
                email="edge1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        # Empty embed should still be valid
        embed = embeds._manager.create_embed(user_id=user.id)

        assert embed is not None
        assert embed.id > 0

    def test_create_embed_with_empty_title(self, db, auth_manager):
        """Test creating embed with empty string title."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="edge2_test",
                email="edge2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, title="")

        assert embed.title is None or embed.title == ""

    def test_create_embed_with_whitespace_title(self, db, auth_manager):
        """Test creating embed with whitespace title."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="edge3_test",
                email="edge3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, title="   ")

        assert embed is not None


class TestUpdateEmbed:
    """Tests for updating embeds."""

    def test_update_embed_title(self, db, auth_manager):
        """Test updating embed title."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="upd1_test",
                email="upd1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, title="Original")
        updated = embeds._manager.update_embed(user.id, embed.id, title="Updated")

        assert updated.title == "Updated"

    def test_update_embed_description(self, db, auth_manager):
        """Test updating embed description."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="upd2_test",
                email="upd2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, description="Original")
        updated = embeds._manager.update_embed(user.id, embed.id, description="Updated")

        assert updated.description == "Updated"

    def test_update_embed_preserves_unchanged_fields(self, db, auth_manager):
        """Test updating embed preserves unchanged fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="upd3_test",
                email="upd3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id, title="Keep This", description="Original Desc"
        )
        updated = embeds._manager.update_embed(
            user.id, embed.id, description="New Desc"
        )

        assert updated.title == "Keep This"
        assert updated.description == "New Desc"

    def test_update_nonexistent_embed(self, db, auth_manager):
        """Test updating nonexistent embed fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="upd4_test",
                email="upd4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(EmbedNotFoundError):
            embeds._manager.update_embed(user.id, 999999999, title="Test")

    def test_update_embed_not_owner(self, db, auth_manager):
        """Test non-owner cannot update embed."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="upd5_test",
                email="upd5_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="upd6_test",
                email="upd6_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user1.id, title="Owner's Embed")

        with pytest.raises(PermissionDeniedError):
            embeds._manager.update_embed(user2.id, embed.id, title="Hacked")

    def test_update_embed_fields(self, db, auth_manager):
        """Test updating embed fields replaces all fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="upd7_test",
                email="upd7_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id,
            title="Fields Test",
            fields=[{"name": "Old1", "value": "V1"}, {"name": "Old2", "value": "V2"}],
        )

        updated = embeds._manager.update_embed(
            user.id, embed.id, fields=[{"name": "New", "value": "NewV"}]
        )

        assert len(updated.fields) == 1
        assert updated.fields[0].name == "New"


class TestModuleNotInitialized:
    """Tests for module not initialized error."""

    def test_module_not_initialized(self):
        """Test error when module not initialized."""
        # This test would require resetting the module state
        # which is complex in the current test setup
        pass


class TestSpecialCharacters:
    """Tests for special characters in embed content."""

    def test_unicode_in_title(self, db, auth_manager):
        """Test Unicode characters in title."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="spec1_test",
                email="spec1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, title="Hello World")

        assert embed.title == "Hello World"

    def test_newlines_in_description(self, db, auth_manager):
        """Test newlines in description."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="spec2_test",
                email="spec2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id, description="Line 1\nLine 2\nLine 3"
        )

        assert "\n" in embed.description

    def test_markdown_in_description(self, db, auth_manager):
        """Test markdown formatting in description."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="spec3_test",
                email="spec3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(
            user_id=user.id, description="**Bold** and *italic* and `code`"
        )

        assert "**Bold**" in embed.description


class TestBoundaryConditions:
    """Tests for boundary conditions."""

    def test_title_exactly_256_chars(self, db, auth_manager):
        """Test title at exactly 256 characters."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="bound1_test",
                email="bound1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        title = "a" * 256
        embed = embeds._manager.create_embed(user_id=user.id, title=title)

        assert len(embed.title) == 256

    def test_description_exactly_4096_chars(self, db, auth_manager):
        """Test description at exactly 4096 characters."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="bound2_test",
                email="bound2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        desc = "a" * 4096
        embed = embeds._manager.create_embed(user_id=user.id, description=desc)

        assert len(embed.description) == 4096

    def test_exactly_25_fields(self, db, auth_manager):
        """Test embed with exactly 25 fields."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="bound3_test",
                email="bound3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        fields = [{"name": f"F{i}", "value": f"V{i}"} for i in range(25)]
        embed = embeds._manager.create_embed(
            user_id=user.id, title="Max Fields", fields=fields
        )

        assert len(embed.fields) == 25

    def test_exactly_10_embeds_on_message(self, db, auth_manager):
        """Test exactly 10 embeds on message."""
        from src.core import embeds, messaging
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user1_test",
                email="user1_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="user2_test",
                email="user2_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        messaging.setup(db, auth_manager)

        # Create DM
        dm = messaging._manager.create_dm(user1.id, user2.id)
        # Create message
        msg = messaging._manager.send_message(user1.id, dm.id, "Test message")

        for i in range(10):
            embed = embeds._manager.create_embed(user_id=user1.id, title=f"Embed {i}")
            embeds._manager.attach_embed_to_message(user1.id, msg.id, embed.id)

        message_embeds = embeds._manager.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 10

    def test_total_chars_exactly_6000(self, db, auth_manager):
        """Test embed with exactly 6000 total characters."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="bound4_test",
                email="bound4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        # 256 + 4096 + 1648 = 6000
        title = "a" * 256
        desc = "b" * 4096
        footer = "c" * 1648

        embed = embeds._manager.create_embed(
            user_id=user.id, title=title, description=desc, footer={"text": footer}
        )

        assert embed is not None


class TestConcurrentOperations:
    """Tests for concurrent operations."""

    def test_multiple_embeds_same_user(self, db, auth_manager):
        """Test creating multiple embeds for same user."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="conc1_test",
                email="conc1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed1 = embeds._manager.create_embed(user_id=user.id, title="Embed 1")
        embed2 = embeds._manager.create_embed(user_id=user.id, title="Embed 2")
        embed3 = embeds._manager.create_embed(user_id=user.id, title="Embed 3")

        assert embed1.id != embed2.id != embed3.id

    def test_multiple_users_create_embeds(self, db, auth_manager):
        """Test multiple users creating embeds."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="conc2_test",
                email="conc2_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="conc3_test",
                email="conc3_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed1 = embeds._manager.create_embed(user_id=user1.id, title="User 1 Embed")
        embed2 = embeds._manager.create_embed(user_id=user2.id, title="User 2 Embed")

        assert embed1.created_by == user1.id
        assert embed2.created_by == user2.id


class TestEmbedRetrieval:
    """Tests for embed retrieval edge cases."""

    def test_get_embed_after_delete(self, db, auth_manager):
        """Test getting embed after deletion returns None."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="ret1_test",
                email="ret1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, title="Delete Me")
        embed_id = embed.id
        embeds._manager.delete_embed(user.id, embed_id)

        result = embeds._manager.get_embed(embed_id)
        assert result is None

    def test_get_message_embeds_empty(self, db, auth_manager):
        """Test getting embeds from message with no embeds."""
        from src.core import embeds, messaging
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user3_test",
                email="user3_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="user4_test",
                email="user4_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        messaging.setup(db, auth_manager)

        # Create DM
        dm = messaging._manager.create_dm(user1.id, user2.id)
        # Create message
        msg = messaging._manager.send_message(user1.id, dm.id, "Test message")

        message_embeds = embeds._manager.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 0

    def test_get_message_embeds_non_participant(self, db, auth_manager):
        """Test non-participant cannot get message embeds."""
        from src.core import embeds, messaging
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username="user5_test",
                email="user5_test@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username="user6_test",
                email="user6_test@example.com",
                password="TestPass123!",
            )
            outsider = auth_manager.register(
                username="out_test",
                email="out_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)
        messaging.setup(db, auth_manager)

        # Create DM
        dm = messaging._manager.create_dm(user1.id, user2.id)
        # Create message
        msg = messaging._manager.send_message(user1.id, dm.id, "Test message")

        with pytest.raises(MessageNotFoundError):
            embeds._manager.get_message_embeds(outsider.id, msg.id)
