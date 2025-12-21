"""
Tests for custom emoji handling.
"""

import pytest
from src.core.reactions import (
    InvalidEmojiError,
    CustomEmojiNotFoundError,
    PermissionDeniedError,
)


class TestCustomEmojiFormat:
    """Tests for custom emoji format parsing."""

    def test_custom_emoji_format_detected(self, users_with_server):
        """Test custom emoji format is detected."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "test_emoji")

        custom_str = f"<:test_emoji:{emoji.id}>"
        reaction = reactions.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.is_custom is True
        assert reaction.custom_emoji_id == emoji.id

    def test_animated_custom_emoji_format(self, users_with_server):
        """Test animated custom emoji format."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "animated_test", animated=True)

        custom_str = f"<a:animated_test:{emoji.id}>"
        reaction = reactions.add_reaction(owner.id, msg.id, custom_str)

        assert reaction.is_custom is True

    def test_invalid_custom_emoji_format(self, users_with_server):
        """Test invalid custom emoji format treated as unicode."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        reaction = reactions.add_reaction(owner.id, msg.id, "<:invalid>")

        assert reaction.is_custom is False
        assert reaction.emoji == "<:invalid>"


class TestCreateCustomEmoji:
    """Tests for creating custom emoji."""

    def test_create_custom_emoji_success(self, users_with_server):
        """Test creating custom emoji successfully."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "new_emoji")

        assert emoji is not None
        assert emoji.name == "new_emoji"
        assert emoji.server_id == server.id
        assert emoji.animated is False
        assert emoji.created_at > 0

    def test_create_animated_emoji(self, users_with_server):
        """Test creating animated custom emoji."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "animated_emoji", animated=True)

        assert emoji.animated is True

    def test_create_emoji_invalid_name_fails(self, users_with_server):
        """Test creating emoji with invalid name fails."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        with pytest.raises(InvalidEmojiError):
            reactions.create_custom_emoji(owner.id, server.id, "a")

        with pytest.raises(InvalidEmojiError):
            reactions.create_custom_emoji(owner.id, server.id, "invalid name")

        with pytest.raises(InvalidEmojiError):
            reactions.create_custom_emoji(owner.id, server.id, "invalid-name")

    def test_create_duplicate_emoji_fails(self, users_with_server):
        """Test creating duplicate emoji name fails."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        reactions.create_custom_emoji(owner.id, server.id, "duplicate_test")

        with pytest.raises(InvalidEmojiError):
            reactions.create_custom_emoji(owner.id, server.id, "duplicate_test")

    def test_create_emoji_no_permission_fails(self, users_with_server):
        """Test member without permission cannot create emoji."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        with pytest.raises(PermissionDeniedError):
            reactions.create_custom_emoji(member.id, server.id, "member_emoji")


class TestDeleteCustomEmoji:
    """Tests for deleting custom emoji."""

    def test_delete_custom_emoji_success(self, users_with_server):
        """Test deleting custom emoji successfully."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "to_delete")
        result = reactions.delete_custom_emoji(owner.id, emoji.id)

        assert result is True

        deleted = reactions.get_custom_emoji(emoji.id)
        assert deleted is None

    def test_delete_emoji_removes_reactions(self, users_with_server):
        """Test deleting emoji removes associated reactions."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "delete_with_reactions")
        custom_str = f"<:delete_with_reactions:{emoji.id}>"

        reactions.add_reaction(owner.id, msg.id, custom_str)

        reactions.delete_custom_emoji(owner.id, emoji.id)

        msg_reactions = reactions.get_reactions(owner.id, msg.id)
        custom_reactions = [r for r in msg_reactions.reactions if r.is_custom and r.custom_emoji_id == emoji.id]
        assert len(custom_reactions) == 0

    def test_delete_nonexistent_emoji_fails(self, users_with_server):
        """Test deleting nonexistent emoji fails."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        with pytest.raises(CustomEmojiNotFoundError):
            reactions.delete_custom_emoji(owner.id, 999999999)

    def test_delete_emoji_no_permission_fails(self, users_with_server):
        """Test member cannot delete emoji."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        emoji = reactions.create_custom_emoji(owner.id, server.id, "no_delete_perm")

        with pytest.raises(PermissionDeniedError):
            reactions.delete_custom_emoji(member.id, emoji.id)


class TestGetCustomEmoji:
    """Tests for getting custom emoji."""

    def test_get_custom_emoji_by_id(self, users_with_server):
        """Test getting custom emoji by ID."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        created = reactions.create_custom_emoji(owner.id, server.id, "get_by_id")
        fetched = reactions.get_custom_emoji(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "get_by_id"

    def test_get_nonexistent_emoji(self, users_with_server):
        """Test getting nonexistent emoji returns None."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        result = reactions.get_custom_emoji(999999999)

        assert result is None

    def test_get_server_custom_emojis(self, users_with_server):
        """Test getting all custom emojis for a server."""
        owner, member, server, group, msg, servers, reactions = users_with_server

        reactions.create_custom_emoji(owner.id, server.id, "server_emoji_1")
        reactions.create_custom_emoji(owner.id, server.id, "server_emoji_2")

        emojis = reactions.get_server_custom_emojis(server.id)

        names = [e.name for e in emojis]
        assert "server_emoji_1" in names
        assert "server_emoji_2" in names


class TestCustomEmojiValidation:
    """Tests for custom emoji validation in reactions."""

    def test_custom_emoji_not_in_server_fails(self, db_and_modules):
        """Test using custom emoji from different server fails."""
        db, auth, messaging, servers_mod, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        owner1 = auth.register(
            username=f"owner1_{unique_id}",
            email=f"owner1_{unique_id}@example.com",
            password="TestPass123!"
        )
        owner2 = auth.register(
            username=f"owner2_{unique_id}",
            email=f"owner2_{unique_id}@example.com",
            password="TestPass123!"
        )

        server1 = servers_mod.create_server(owner1.id, f"Server1 {unique_id}")
        server2 = servers_mod.create_server(owner2.id, f"Server2 {unique_id}")
        servers_mod.add_member(server2.id, owner1.id)

        emoji = reactions.create_custom_emoji(owner1.id, server1.id, f"server1_only_{unique_id}")

        channel2 = servers_mod.get_channels(owner2.id, server2.id)[0]
        msg = servers_mod.send_channel_message(owner2.id, channel2.id, "Test message")

        custom_str = f"<:server1_only_{unique_id}:{emoji.id}>"

        with pytest.raises(CustomEmojiNotFoundError):
            reactions.add_reaction(owner1.id, msg.id, custom_str)
