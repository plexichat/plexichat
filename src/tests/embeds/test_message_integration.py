"""
Tests for attaching, updating, and removing embeds from messages.
"""

import pytest
from src.core.embeds import (
    EmbedNotFoundError,
    EmbedLimitError,
    MessageNotFoundError,
    PermissionDeniedError,
)
from unittest.mock import patch


class TestAttachEmbedToMessage:
    """Tests for attaching embeds to messages."""

    def test_attach_embed_to_message(self, fresh_users_with_dm):
        """Test attaching embed to message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Attached Embed")
        result = embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        assert result is True

    def test_get_message_embeds(self, fresh_users_with_dm):
        """Test getting embeds attached to message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Get Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)

        assert len(message_embeds) == 1
        assert message_embeds[0].title == "Get Test"

    def test_attach_multiple_embeds(self, fresh_users_with_dm):
        """Test attaching multiple embeds to message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed1 = embeds.create_embed(user_id=user1.id, title="Embed 1")
        embed2 = embeds.create_embed(user_id=user1.id, title="Embed 2")
        embed3 = embeds.create_embed(user_id=user1.id, title="Embed 3")

        embeds.attach_embed_to_message(user1.id, msg.id, embed1.id)
        embeds.attach_embed_to_message(user1.id, msg.id, embed2.id)
        embeds.attach_embed_to_message(user1.id, msg.id, embed3.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)

        assert len(message_embeds) == 3

    def test_attach_embed_max_limit(self, fresh_users_with_dm):
        """Test attaching max 10 embeds to message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        for i in range(10):
            embed = embeds.create_embed(user_id=user1.id, title=f"Embed {i}")
            embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 10

    def test_attach_embed_exceeds_limit(self, fresh_users_with_dm):
        """Test attaching more than 10 embeds fails."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        for i in range(10):
            embed = embeds.create_embed(user_id=user1.id, title=f"Embed {i}")
            embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        extra_embed = embeds.create_embed(user_id=user1.id, title="Extra")

        with pytest.raises(EmbedLimitError) as exc_info:
            embeds.attach_embed_to_message(user1.id, msg.id, extra_embed.id)

        assert exc_info.value.max_allowed == 10
        assert exc_info.value.current == 10

    def test_attach_embed_nonexistent_message(self, db, auth_manager):
        """Test attaching embed to nonexistent message fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="att1_test",
                email="att1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        embed = embeds._manager.create_embed(user_id=user.id, title="Test")

        with pytest.raises(MessageNotFoundError):
            embeds._manager.attach_embed_to_message(user.id, 999999999, embed.id)

    def test_attach_nonexistent_embed(self, fresh_users_with_dm):
        """Test attaching nonexistent embed fails."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        with pytest.raises(EmbedNotFoundError):
            embeds.attach_embed_to_message(user1.id, msg.id, 999999999)

    def test_attach_embed_not_message_author(self, fresh_users_with_dm):
        """Test non-author cannot attach embed."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user2.id, title="Other User Embed")

        with pytest.raises(PermissionDeniedError):
            embeds.attach_embed_to_message(user2.id, msg.id, embed.id)

    def test_attach_same_embed_twice(self, fresh_users_with_dm):
        """Test attaching same embed twice is idempotent."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Duplicate Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)
        result = embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        assert result is True
        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 1


class TestRemoveEmbedFromMessage:
    """Tests for removing embeds from messages."""

    def test_remove_embed_from_message(self, fresh_users_with_dm):
        """Test removing embed from message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Remove Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        result = embeds.remove_embed_from_message(user1.id, msg.id, embed.id)

        assert result is True
        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 0

    def test_remove_one_of_multiple_embeds(self, fresh_users_with_dm):
        """Test removing one embed leaves others."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed1 = embeds.create_embed(user_id=user1.id, title="Keep 1")
        embed2 = embeds.create_embed(user_id=user1.id, title="Remove")
        embed3 = embeds.create_embed(user_id=user1.id, title="Keep 2")

        embeds.attach_embed_to_message(user1.id, msg.id, embed1.id)
        embeds.attach_embed_to_message(user1.id, msg.id, embed2.id)
        embeds.attach_embed_to_message(user1.id, msg.id, embed3.id)

        embeds.remove_embed_from_message(user1.id, msg.id, embed2.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 2
        titles = [e.title for e in message_embeds]
        assert "Remove" not in titles

    def test_remove_embed_not_message_author(self, fresh_users_with_dm):
        """Test non-author cannot remove embed."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        with pytest.raises(PermissionDeniedError):
            embeds.remove_embed_from_message(user2.id, msg.id, embed.id)

    def test_remove_embed_nonexistent_message(self, db, auth_manager):
        """Test removing embed from nonexistent message fails."""
        from src.core import embeds
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username="rem1_test",
                email="rem1_test@example.com",
                password="TestPass123!",
            )

        embeds.setup(db, None, None)

        with pytest.raises(MessageNotFoundError):
            embeds._manager.remove_embed_from_message(user.id, 999999999, 123)


class TestUpdateEmbedOnMessage:
    """Tests for updating embeds attached to messages."""

    def test_update_attached_embed(self, fresh_users_with_dm):
        """Test updating embed that is attached to message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Original")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        embeds.update_embed(user1.id, embed.id, title="Updated")

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert message_embeds[0].title == "Updated"

    def test_update_embed_fields(self, fresh_users_with_dm):
        """Test updating embed fields."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(
            user_id=user1.id,
            title="Fields Test",
            fields=[{"name": "Old", "value": "Old Value"}],
        )
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        embeds.update_embed(
            user1.id, embed.id, fields=[{"name": "New", "value": "New Value"}]
        )

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert message_embeds[0].fields[0].name == "New"


class TestSuppressEmbeds:
    """Tests for suppressing embeds on messages."""

    def test_suppress_embeds(self, fresh_users_with_dm):
        """Test suppressing embeds hides them."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Suppress Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        result = embeds.suppress_embeds(user1.id, msg.id)

        assert result is True
        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 0

    def test_unsuppress_embeds(self, fresh_users_with_dm):
        """Test unsuppressing embeds shows them again."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Unsuppress Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)
        embeds.suppress_embeds(user1.id, msg.id)

        result = embeds.unsuppress_embeds(user1.id, msg.id)

        assert result is True
        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 1

    def test_suppress_embeds_not_author(self, fresh_users_with_dm):
        """Test non-author cannot suppress embeds."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        with pytest.raises(PermissionDeniedError):
            embeds.suppress_embeds(user2.id, msg.id)

    def test_unsuppress_embeds_not_author(self, fresh_users_with_dm):
        """Test non-author cannot unsuppress embeds."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)
        embeds.suppress_embeds(user1.id, msg.id)

        with pytest.raises(PermissionDeniedError):
            embeds.unsuppress_embeds(user2.id, msg.id)


class TestEmbedOrdering:
    """Tests for embed ordering on messages."""

    def test_embeds_maintain_order(self, fresh_users_with_dm):
        """Test embeds maintain attachment order."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed1 = embeds.create_embed(user_id=user1.id, title="First")
        embed2 = embeds.create_embed(user_id=user1.id, title="Second")
        embed3 = embeds.create_embed(user_id=user1.id, title="Third")

        embeds.attach_embed_to_message(user1.id, msg.id, embed1.id)
        embeds.attach_embed_to_message(user1.id, msg.id, embed2.id)
        embeds.attach_embed_to_message(user1.id, msg.id, embed3.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)

        assert message_embeds[0].title == "First"
        assert message_embeds[1].title == "Second"
        assert message_embeds[2].title == "Third"

    def test_attach_embed_with_position(self, fresh_users_with_dm):
        """Test attaching embed at specific position."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed1 = embeds.create_embed(user_id=user1.id, title="First")
        embed2 = embeds.create_embed(user_id=user1.id, title="Inserted")

        embeds.attach_embed_to_message(user1.id, msg.id, embed1.id, position=0)
        embeds.attach_embed_to_message(user1.id, msg.id, embed2.id, position=0)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)

        # Position 0 should be "Inserted" since it was added with position=0
        assert message_embeds[0].title == "Inserted"


class TestDeleteEmbed:
    """Tests for deleting embeds."""

    def test_delete_embed(self, db, auth_manager, embeds_manager):
        """Test deleting an embed."""
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username=f"del1_{unique_id}",
                email=f"del1_{unique_id}@example.com",
                password="TestPass123!",
            )

        embed = embeds_manager.create_embed(user_id=user.id, title="Delete Me")
        result = embeds_manager.delete_embed(user.id, embed.id)

        assert result is True
        assert embeds_manager.get_embed(embed.id) is None

    def test_delete_embed_removes_from_messages(self, fresh_users_with_dm):
        """Test deleting embed removes it from messages."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        embed = embeds.create_embed(user_id=user1.id, title="Delete Test")
        embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        embeds.delete_embed(user1.id, embed.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 0

    def test_delete_embed_not_owner(self, db, auth_manager, embeds_manager):
        """Test non-owner cannot delete embed."""
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"del2_{unique_id}",
                email=f"del2_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"del3_{unique_id}",
                email=f"del3_{unique_id}@example.com",
                password="TestPass123!",
            )

        embed = embeds_manager.create_embed(user_id=user1.id, title="Not Yours")

        with pytest.raises(PermissionDeniedError):
            embeds_manager.delete_embed(user2.id, embed.id)

    def test_delete_nonexistent_embed(self, db, auth_manager, embeds_manager):
        """Test deleting nonexistent embed fails."""
        from src.utils import encryption
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username=f"del4_{unique_id}",
                email=f"del4_{unique_id}@example.com",
                password="TestPass123!",
            )

        with pytest.raises(EmbedNotFoundError):
            embeds_manager.delete_embed(user.id, 999999999)
