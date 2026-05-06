"""Tests for messaging conversation management."""

import pytest

from src.core.messaging.models import ConversationType


@pytest.mark.messaging
class TestConversations:
    """Tests for conversation creation, retrieval, and management."""

    def test_create_dm(self, messaging_manager, two_users):
        """Test creating a DM conversation."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        assert dm is not None
        assert dm.conversation_type == ConversationType.DM

    def test_create_dm_idempotent(self, messaging_manager, two_users):
        """Test that creating a DM with same users returns existing DM."""
        user1, user2 = two_users
        dm1 = messaging_manager.create_dm(user1.id, user2.id)
        dm2 = messaging_manager.create_dm(user1.id, user2.id)
        assert dm1.id == dm2.id

    def test_get_conversation(self, messaging_manager, two_users):
        """Test getting a conversation by ID."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        retrieved = messaging_manager.get_conversation(dm.id, user1.id)
        assert retrieved is not None
        assert retrieved.id == dm.id

    def test_get_conversation_non_participant(self, messaging_manager, three_users):
        """Test that non-participants cannot get a conversation."""
        user1, user2, user3 = three_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        result = messaging_manager.get_conversation(dm.id, user3.id)
        assert result is None

    def test_create_group(self, messaging_manager, three_users):
        """Test creating a group conversation."""
        owner, member1, member2 = three_users
        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )
        assert group is not None
        assert group.conversation_type == ConversationType.GROUP
        assert group.name == "Test Group"

    def test_update_conversation_name(self, messaging_manager, three_users):
        """Test updating a group conversation name."""
        owner, member1, member2 = three_users
        group = messaging_manager.create_group(
            owner.id, "Original Name", [member1.id, member2.id]
        )
        updated = messaging_manager.update_conversation(
            owner.id, group.id, name="New Name"
        )
        assert updated.name == "New Name"

    def test_delete_conversation(self, messaging_manager, two_users):
        """Test deleting a conversation."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        result = messaging_manager.delete_conversation(user1.id, dm.id)
        assert result is True

    def test_get_user_conversations(self, messaging_manager, two_users):
        """Test getting all conversations for a user."""
        user1, user2 = two_users
        messaging_manager.create_dm(user1.id, user2.id)
        conversations = messaging_manager.get_conversations(user1.id)
        assert len(conversations) >= 1

    def test_leave_group_conversation(self, messaging_manager, three_users):
        """Test leaving a group conversation."""
        owner, member1, member2 = three_users
        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )
        result = messaging_manager.leave_conversation(member1.id, group.id)
        assert result is True
