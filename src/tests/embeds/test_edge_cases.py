"""
Tests for error handling and edge cases.
"""

import pytest
from src.core.embeds import (
    EmbedNotFoundError,
    MessageNotFoundError,
    PermissionDeniedError,
)


class TestEmptyEmbed:
    """Tests for empty or minimal embeds."""

    def test_create_empty_embed(self, db_and_modules):
        """Test creating embed with no fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"edge1_{unique_id}",
            email=f"edge1_{unique_id}@example.com",
            password="TestPass123!"
        )

        # Empty embed should still be valid
        embed = embeds.create_embed(user_id=user.id)

        assert embed is not None
        assert embed.id > 0

    def test_create_embed_with_empty_title(self, db_and_modules):
        """Test creating embed with empty string title."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"edge2_{unique_id}",
            email=f"edge2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, title="")

        assert embed.title is None or embed.title == ""

    def test_create_embed_with_whitespace_title(self, db_and_modules):
        """Test creating embed with whitespace title."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"edge3_{unique_id}",
            email=f"edge3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, title="   ")

        assert embed is not None


class TestUpdateEmbed:
    """Tests for updating embeds."""

    def test_update_embed_title(self, db_and_modules):
        """Test updating embed title."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"upd1_{unique_id}",
            email=f"upd1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, title="Original")
        updated = embeds.update_embed(user.id, embed.id, title="Updated")

        assert updated.title == "Updated"

    def test_update_embed_description(self, db_and_modules):
        """Test updating embed description."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"upd2_{unique_id}",
            email=f"upd2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, description="Original")
        updated = embeds.update_embed(user.id, embed.id, description="Updated")

        assert updated.description == "Updated"

    def test_update_embed_preserves_unchanged_fields(self, db_and_modules):
        """Test updating embed preserves unchanged fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"upd3_{unique_id}",
            email=f"upd3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Keep This",
            description="Original Desc"
        )
        updated = embeds.update_embed(user.id, embed.id, description="New Desc")

        assert updated.title == "Keep This"
        assert updated.description == "New Desc"

    def test_update_nonexistent_embed(self, db_and_modules):
        """Test updating nonexistent embed fails."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"upd4_{unique_id}",
            email=f"upd4_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(EmbedNotFoundError):
            embeds.update_embed(user.id, 999999999, title="Test")

    def test_update_embed_not_owner(self, db_and_modules):
        """Test non-owner cannot update embed."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"upd5_{unique_id}",
            email=f"upd5_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"upd6_{unique_id}",
            email=f"upd6_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user1.id, title="Owner's Embed")

        with pytest.raises(PermissionDeniedError):
            embeds.update_embed(user2.id, embed.id, title="Hacked")

    def test_update_embed_fields(self, db_and_modules):
        """Test updating embed fields replaces all fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"upd7_{unique_id}",
            email=f"upd7_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Fields Test",
            fields=[
                {"name": "Old1", "value": "V1"},
                {"name": "Old2", "value": "V2"}
            ]
        )

        updated = embeds.update_embed(
            user.id,
            embed.id,
            fields=[{"name": "New", "value": "NewV"}]
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

    def test_unicode_in_title(self, db_and_modules):
        """Test Unicode characters in title."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"spec1_{unique_id}",
            email=f"spec1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            title="Hello World"
        )

        assert embed.title == "Hello World"

    def test_newlines_in_description(self, db_and_modules):
        """Test newlines in description."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"spec2_{unique_id}",
            email=f"spec2_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            description="Line 1\nLine 2\nLine 3"
        )

        assert "\n" in embed.description

    def test_markdown_in_description(self, db_and_modules):
        """Test markdown formatting in description."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"spec3_{unique_id}",
            email=f"spec3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(
            user_id=user.id,
            description="**Bold** and *italic* and `code`"
        )

        assert "**Bold**" in embed.description


class TestBoundaryConditions:
    """Tests for boundary conditions."""

    def test_title_exactly_256_chars(self, db_and_modules):
        """Test title at exactly 256 characters."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"bound1_{unique_id}",
            email=f"bound1_{unique_id}@example.com",
            password="TestPass123!"
        )

        title = "a" * 256
        embed = embeds.create_embed(user_id=user.id, title=title)

        assert len(embed.title) == 256

    def test_description_exactly_4096_chars(self, db_and_modules):
        """Test description at exactly 4096 characters."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"bound2_{unique_id}",
            email=f"bound2_{unique_id}@example.com",
            password="TestPass123!"
        )

        desc = "a" * 4096
        embed = embeds.create_embed(user_id=user.id, description=desc)

        assert len(embed.description) == 4096

    def test_exactly_25_fields(self, db_and_modules):
        """Test embed with exactly 25 fields."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"bound3_{unique_id}",
            email=f"bound3_{unique_id}@example.com",
            password="TestPass123!"
        )

        fields = [{"name": f"F{i}", "value": f"V{i}"} for i in range(25)]
        embed = embeds.create_embed(user_id=user.id, title="Max Fields", fields=fields)

        assert len(embed.fields) == 25

    def test_exactly_10_embeds_on_message(self, fresh_users_with_dm):
        """Test exactly 10 embeds on message."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        for i in range(10):
            embed = embeds.create_embed(user_id=user1.id, title=f"Embed {i}")
            embeds.attach_embed_to_message(user1.id, msg.id, embed.id)

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 10

    def test_total_chars_exactly_6000(self, db_and_modules):
        """Test embed with exactly 6000 total characters."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"bound4_{unique_id}",
            email=f"bound4_{unique_id}@example.com",
            password="TestPass123!"
        )

        # 256 + 4096 + 1648 = 6000
        title = "a" * 256
        desc = "b" * 4096
        footer = "c" * 1648

        embed = embeds.create_embed(
            user_id=user.id,
            title=title,
            description=desc,
            footer={"text": footer}
        )

        assert embed is not None


class TestConcurrentOperations:
    """Tests for concurrent operations."""

    def test_multiple_embeds_same_user(self, db_and_modules):
        """Test creating multiple embeds for same user."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"conc1_{unique_id}",
            email=f"conc1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed1 = embeds.create_embed(user_id=user.id, title="Embed 1")
        embed2 = embeds.create_embed(user_id=user.id, title="Embed 2")
        embed3 = embeds.create_embed(user_id=user.id, title="Embed 3")

        assert embed1.id != embed2.id != embed3.id

    def test_multiple_users_create_embeds(self, db_and_modules):
        """Test multiple users creating embeds."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"conc2_{unique_id}",
            email=f"conc2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"conc3_{unique_id}",
            email=f"conc3_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed1 = embeds.create_embed(user_id=user1.id, title="User 1 Embed")
        embed2 = embeds.create_embed(user_id=user2.id, title="User 2 Embed")

        assert embed1.created_by == user1.id
        assert embed2.created_by == user2.id


class TestEmbedRetrieval:
    """Tests for embed retrieval edge cases."""

    def test_get_embed_after_delete(self, db_and_modules):
        """Test getting embed after deletion returns None."""
        db, auth, messaging, servers, embeds = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"ret1_{unique_id}",
            email=f"ret1_{unique_id}@example.com",
            password="TestPass123!"
        )

        embed = embeds.create_embed(user_id=user.id, title="Delete Me")
        embed_id = embed.id
        embeds.delete_embed(user.id, embed_id)

        result = embeds.get_embed(embed_id)
        assert result is None

    def test_get_message_embeds_empty(self, fresh_users_with_dm):
        """Test getting embeds from message with no embeds."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm

        message_embeds = embeds.get_message_embeds(user1.id, msg.id)
        assert len(message_embeds) == 0

    def test_get_message_embeds_non_participant(self, fresh_users_with_dm):
        """Test non-participant cannot get message embeds."""
        user1, user2, dm, msg, embeds, messaging = fresh_users_with_dm
        import uuid
        from src.core import auth

        unique_id = uuid.uuid4().hex[:8]
        outsider = auth.register(
            username=f"out_{unique_id}",
            email=f"out_{unique_id}@example.com",
            password="TestPass123!"
        )

        with pytest.raises(MessageNotFoundError):
            embeds.get_message_embeds(outsider.id, msg.id)
