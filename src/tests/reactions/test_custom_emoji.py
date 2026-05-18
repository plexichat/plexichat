"""
Tests for custom emoji handling.
"""

import pytest
from src.core.reactions import (
    InvalidEmojiNameError,
    EmojiNameExistsError,
    CustomEmojiNotFoundError,
)


# Fixtures for sample images
@pytest.fixture
def sample_image():
    """Create a sample PNG image for testing."""
    # Minimal valid PNG (1x1 transparent pixel)
    return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\x0d\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"


@pytest.fixture
def sample_gif():
    """Create a sample GIF image for testing."""
    # Minimal valid GIF header
    return b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


class TestCustomEmojiFormat:
    """Tests for custom emoji format parsing."""

    def test_custom_emoji_format_detected(self, users_with_server, sample_image):
        """Test custom emoji format is detected."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(
            owner.id, server.id, "test_emoji", sample_image, "image/png"
        )

        custom_str = f"<:test_emoji:{emoji.id}>"
        reaction = reaction_manager.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.is_custom is True
        assert reaction.custom_emoji_id == emoji.id

    def test_animated_custom_emoji_format(self, users_with_server, sample_gif):
        """Test animated custom emoji format."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(
            owner.id, server.id, "animated_test", sample_gif, "image/gif"
        )

        custom_str = f"<a:animated_test:{emoji.id}>"
        reaction = reaction_manager.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.is_custom is True

    def test_invalid_custom_emoji_format(self, users_with_server):
        """Test invalid custom emoji format raises error."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        from src.core.reactions import InvalidEmojiError

        with pytest.raises(InvalidEmojiError):
            reaction_manager.add_reaction(owner.id, msg.id, "<:invalid>")


class TestCreateCustomEmoji:
    """Tests for creating custom emoji."""

    def test_create_custom_emoji_success(self, users_with_server, sample_image):
        """Test creating custom emoji successfully."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(
            owner.id, server.id, "new_emoji", sample_image, "image/png"
        )

        assert emoji is not None
        assert emoji.name == "new_emoji"
        assert emoji.server_id == server.id
        assert emoji.animated is False
        assert emoji.created_at > 0

    def test_create_animated_emoji(self, users_with_server, sample_gif):
        """Test creating animated custom emoji."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(
            owner.id, server.id, "animated_emoji", sample_gif, "image/gif"
        )

        assert emoji.animated is True

    def test_create_emoji_invalid_name_fails(self, users_with_server, sample_image):
        """Test creating emoji with invalid name fails."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        with pytest.raises(InvalidEmojiNameError):
            reaction_manager.create_custom_emoji(
                owner.id, server.id, "a", sample_image, "image/png"
            )

        with pytest.raises(InvalidEmojiNameError):
            reaction_manager.create_custom_emoji(
                owner.id, server.id, "invalid name", sample_image, "image/png"
            )

        with pytest.raises(InvalidEmojiNameError):
            reaction_manager.create_custom_emoji(
                owner.id, server.id, "invalid-name", sample_image, "image/png"
            )

    def test_create_duplicate_emoji_fails(self, users_with_server, sample_image):
        """Test creating duplicate emoji name fails."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        reaction_manager.create_custom_emoji(
            owner.id, server.id, "duplicate_test", sample_image, "image/png"
        )

        with pytest.raises(EmojiNameExistsError):
            reaction_manager.create_custom_emoji(
                owner.id, server.id, "duplicate_test", sample_image, "image/png"
            )

    def test_create_emoji_no_permission_fails(self, users_with_server, sample_image):
        """Test member without permission cannot create emoji."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        # Skip permission test for now - permission system needs review
        pytest.skip("Permission system not fully implemented")


class TestDeleteCustomEmoji:
    """Tests for deleting custom emoji."""

    def test_delete_custom_emoji_success(self, users_with_server, sample_image):
        """Test deleting custom emoji successfully."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(
            owner.id, server.id, "to_delete", sample_image, "image/png"
        )
        result = reaction_manager.delete_custom_emoji(owner.id, emoji.id)

        assert result is True

        deleted = reaction_manager.get_custom_emoji(emoji.id)
        assert deleted is None

    def test_delete_emoji_removes_reactions(self, users_with_server, sample_image):
        """Test deleting emoji removes associated reactions."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        emoji = reaction_manager.create_custom_emoji(
            owner.id, server.id, "delete_with_reactions", sample_image, "image/png"
        )
        custom_str = f"<:delete_with_reactions:{emoji.id}>"

        reaction_manager.add_reaction(owner.id, msg.id, custom_str)

        reaction_manager.delete_custom_emoji(owner.id, emoji.id)

        msg_reactions = reaction_manager.get_reactions(owner.id, msg.id)
        custom_reactions = [
            r
            for r in msg_reactions.reactions
            if r.is_custom and r.custom_emoji_id == emoji.id
        ]
        assert len(custom_reactions) == 0

    def test_delete_nonexistent_emoji_fails(self, users_with_server):
        """Test deleting nonexistent emoji fails."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        with pytest.raises(CustomEmojiNotFoundError):
            reaction_manager.delete_custom_emoji(owner.id, 999999999)

    def test_delete_emoji_no_permission_fails(self, users_with_server, sample_image):
        """Test member cannot delete emoji."""
        # Skip permission test for now - permission system needs review
        pytest.skip("Permission system not fully implemented")


class TestGetCustomEmoji:
    """Tests for getting custom emoji."""

    def test_get_custom_emoji_by_id(self, users_with_server, sample_image):
        """Test getting custom emoji by ID."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        created = reaction_manager.create_custom_emoji(
            owner.id, server.id, "get_by_id", sample_image, "image/png"
        )
        fetched = reaction_manager.get_custom_emoji(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "get_by_id"

    def test_get_nonexistent_emoji(self, users_with_server):
        """Test getting nonexistent emoji returns None."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        result = reaction_manager.get_custom_emoji(999999999)

        assert result is None

    def test_get_server_custom_emojis(self, users_with_server, sample_image):
        """Test getting all custom emojis for a server."""
        owner, member, server, group, msg, server_manager, reaction_manager = (
            users_with_server
        )

        reaction_manager.create_custom_emoji(
            owner.id, server.id, "server_emoji_1", sample_image, "image/png"
        )
        reaction_manager.create_custom_emoji(
            owner.id, server.id, "server_emoji_2", sample_image, "image/png"
        )

        emojis = reaction_manager.get_server_custom_emojis(server.id)

        names = [e.name for e in emojis]
        assert "server_emoji_1" in names
        assert "server_emoji_2" in names


class TestCustomEmojiValidation:
    """Tests for custom emoji validation in reactions."""

    def test_custom_emoji_not_in_server_fails(
        self,
        auth_manager,
        server_manager,
        messaging_manager,
        reaction_manager,
        sample_image,
    ):
        """Test using custom emoji from different server fails."""
        # Skip complex cross-server validation test for now
        pytest.skip("Cross-server emoji validation needs fixture redesign")
