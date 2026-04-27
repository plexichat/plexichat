"""Tests for messaging group conversations."""

import pytest

from src.core.messaging.models import ConversationType, ParticipantRole
from src.core.messaging.exceptions import (
    ParticipantLimitError,
    ConversationAccessDeniedError,
)


@pytest.mark.messaging
class TestGroups:
    """Tests for group conversation management."""

    def test_create_group_with_name(self, messaging_manager, three_users):
        """Test creating a group with a name."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "My Group", [m1.id, m2.id])
        assert group.name == "My Group"
        assert group.conversation_type == ConversationType.GROUP

    def test_create_group_with_owner_only(self, messaging_manager, test_user):
        """Test creating a group with just the owner."""
        group = messaging_manager.create_group(test_user.id, "Solo Group")
        assert group is not None
        assert group.owner_id == test_user.id

    def test_group_owner_is_participant(self, messaging_manager, three_users):
        """Test that the group owner is automatically a participant."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "Test Group", [m1.id, m2.id])
        participants = messaging_manager.get_participants(owner.id, group.id)
        owner_participant = [p for p in participants if p.user_id == owner.id]
        assert len(owner_participant) == 1

    def test_update_group_name_by_owner(self, messaging_manager, three_users):
        """Test that the owner can update the group name."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "Old Name", [m1.id, m2.id])
        updated = messaging_manager.update_conversation(
            owner.id, group.id, name="New Name"
        )
        assert updated.name == "New Name"

    def test_group_owner_has_owner_role(self, messaging_manager, three_users):
        """Test that the group owner has the OWNER participant role."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "Test", [m1.id, m2.id])
        participants = messaging_manager.get_participants(owner.id, group.id)
        owner_p = next(p for p in participants if p.user_id == owner.id)
        assert owner_p.role == ParticipantRole.OWNER

    def test_leave_group_as_member(self, messaging_manager, three_users):
        """Test that a member can leave a group."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "Test", [m1.id, m2.id])
        result = messaging_manager.leave_conversation(m1.id, group.id)
        assert result is True

    def test_delete_group(self, messaging_manager, three_users):
        """Test deleting a group conversation."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "Test", [m1.id, m2.id])
        result = messaging_manager.delete_conversation(owner.id, group.id)
        assert result is True

    def test_system_message_on_group_creation(self, messaging_manager, three_users):
        """Test that a system message is sent when a group is created."""
        owner, m1, m2 = three_users
        group = messaging_manager.create_group(owner.id, "Test", [m1.id, m2.id])
        messages = messaging_manager.get_messages(owner.id, group.id)
        system_msgs = [m for m in messages if m.message_type.value == "system"]
        # At least one system message should be present
        assert len(system_msgs) >= 1
