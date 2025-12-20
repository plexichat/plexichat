"""
Group conversation tests.

Tests group-specific functionality, permissions, roles,
and group management.
"""

import pytest
from src.core.messaging.exceptions import (
    InvalidContentError,
    ParticipantLimitError,
    ConversationAccessDeniedError,
)
from src.core.messaging.models import ConversationType, ParticipantRole


class TestGroupCreation:
    """Tests for group creation."""

    def test_create_basic_group(self, user_pool, modules):
        """Test creating a basic group."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Test Group")

        assert group.conversation_type == ConversationType.GROUP
        assert group.name == "Test Group"
        assert group.owner_id == owner.id

    def test_create_group_with_members(self, user_pool, modules):
        """Test creating group with initial members."""
        owner = user_pool.get_user()
        member1 = user_pool.get_user()
        member2 = user_pool.get_user()

        group = modules.messaging.create_group(
            owner_id=owner.id,
            name="Group with Members",
            participant_ids=[member1.id, member2.id],
        )

        assert group.participant_count == 3

    def test_create_group_empty_name_fails(self, user_pool, modules):
        """Test that group with empty name fails."""
        owner = user_pool.get_user()

        with pytest.raises(InvalidContentError):
            modules.messaging.create_group(owner_id=owner.id, name="")

    def test_create_group_long_name_fails(self, user_pool, modules):
        """Test that group with too long name fails."""
        owner = user_pool.get_user()

        with pytest.raises(InvalidContentError):
            modules.messaging.create_group(owner_id=owner.id, name="A" * 200)

    def test_create_group_with_max_participants(self, user_pool, modules):
        """Test creating group with custom max participants."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(
            owner_id=owner.id, name="Limited Group", max_participants=10
        )

        assert group.max_participants == 10

    def test_create_group_exceeding_limit_fails(self, user_pool, modules):
        """Test that creating group exceeding limit fails."""
        owner = user_pool.get_user()

        # Try to add more members than limit
        member_ids = [user_pool.get_user().id for _ in range(15)]

        with pytest.raises(ParticipantLimitError):
            modules.messaging.create_group(
                owner_id=owner.id,
                name="Overcrowded",
                participant_ids=member_ids,
                max_participants=10,
            )

    def test_group_creates_system_message(self, user_pool, modules):
        """Test that group creation creates system message."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Test Group")

        # Check for system message
        messages = modules.messaging.get_messages(owner.id, group.id, limit=10)
        assert len(messages) >= 1
        assert any(m.message_type.value == "system" for m in messages)


class TestGroupRoles:
    """Tests for group roles and permissions."""

    def test_owner_role_on_creation(self, group_conversation):
        """Test that creator gets owner role."""
        group, owner, member1, member2, messaging = group_conversation

        participants = messaging.get_participants(owner.id, group.id)
        owner_participant = next(p for p in participants if p.user_id == owner.id)

        assert owner_participant.role == ParticipantRole.OWNER

    def test_members_get_member_role(self, group_conversation):
        """Test that added members get member role."""
        group, owner, member1, member2, messaging = group_conversation

        participants = messaging.get_participants(owner.id, group.id)
        member_participant = next(p for p in participants if p.user_id == member1.id)

        assert member_participant.role == ParticipantRole.MEMBER

    def test_promote_member_to_admin(self, group_conversation):
        """Test promoting member to admin."""
        group, owner, member1, member2, messaging = group_conversation

        updated = messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        assert updated.role == ParticipantRole.ADMIN

    def test_demote_admin_to_member(self, group_conversation):
        """Test demoting admin to member."""
        group, owner, member1, member2, messaging = group_conversation

        # Promote first
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        # Then demote
        updated = messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.MEMBER
        )

        assert updated.role == ParticipantRole.MEMBER

    def test_admin_can_add_members(self, group_conversation, user_pool):
        """Test that admins can add members."""
        group, owner, member1, member2, messaging = group_conversation

        # Promote member1 to admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        # Admin adds new member
        new_user = user_pool.get_user()
        result = messaging.add_participant(member1.id, group.id, new_user.id)

        assert result is not None

    def test_admin_can_remove_members(self, group_conversation):
        """Test that admins can remove regular members."""
        group, owner, member1, member2, messaging = group_conversation

        # Promote member1 to admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        # Admin removes member
        result = messaging.remove_participant(member1.id, group.id, member2.id)
        assert result is True

    def test_admin_cannot_remove_owner(self, group_conversation):
        """Test that admins cannot remove owner."""
        group, owner, member1, member2, messaging = group_conversation

        # Promote member1 to admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging.remove_participant(member1.id, group.id, owner.id)


class TestGroupManagement:
    """Tests for group management operations."""

    def test_update_group_name(self, group_conversation):
        """Test updating group name."""
        group, owner, member1, member2, messaging = group_conversation

        updated = messaging.update_conversation(owner.id, group.id, name="New Name")

        assert updated.name == "New Name"

    def test_member_cannot_update_group(self, group_conversation):
        """Test that members cannot update group."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.update_conversation(member1.id, group.id, name="Unauthorized")

    def test_admin_can_update_group(self, group_conversation):
        """Test that admins can update group."""
        group, owner, member1, member2, messaging = group_conversation

        # Promote to admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        updated = messaging.update_conversation(
            member1.id, group.id, name="Admin Update"
        )

        assert updated.name == "Admin Update"

    def test_increase_max_participants(self, group_conversation):
        """Test increasing max participants."""
        group, owner, member1, member2, messaging = group_conversation

        updated = messaging.update_conversation(
            owner.id, group.id, max_participants=200
        )

        assert updated.max_participants == 200

    def test_cannot_reduce_max_below_current(self, group_conversation):
        """Test that max cannot be reduced below current count."""
        group, owner, member1, member2, messaging = group_conversation

        # Current count is 3
        with pytest.raises(ParticipantLimitError):
            messaging.update_conversation(owner.id, group.id, max_participants=2)


class TestGroupDeletion:
    """Tests for group deletion."""

    def test_owner_can_delete_group(self, group_conversation):
        """Test that owner can delete group."""
        group, owner, member1, member2, messaging = group_conversation

        result = messaging.delete_conversation(owner.id, group.id)
        assert result is True

        # Verify deleted
        conv = messaging.get_conversation(group.id, owner.id)
        assert conv is None

    def test_member_cannot_delete_group(self, group_conversation):
        """Test that members cannot delete group."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.delete_conversation(member1.id, group.id)

    def test_admin_cannot_delete_group(self, group_conversation):
        """Test that admins cannot delete group."""
        group, owner, member1, member2, messaging = group_conversation

        # Promote to admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging.delete_conversation(member1.id, group.id)

    def test_member_can_leave_group(self, group_conversation):
        """Test that members can leave group."""
        group, owner, member1, member2, messaging = group_conversation

        result = messaging.leave_conversation(member1.id, group.id)
        assert result is True

        # Verify left
        conv = messaging.get_conversation(group.id, member1.id)
        assert conv is None

    def test_owner_leave_transfers_ownership(self, group_conversation):
        """Test that owner leaving transfers ownership."""
        group, owner, member1, member2, messaging = group_conversation

        messaging.leave_conversation(owner.id, group.id)

        # Group still exists with new owner
        conv = messaging.get_conversation(group.id, member1.id)
        assert conv is not None
        assert conv.owner_id != owner.id

    def test_last_member_leave_deletes_group(self, user_pool, modules):
        """Test that last member leaving deletes group."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Solo Group")

        modules.messaging.leave_conversation(owner.id, group.id)

        # Group should be deleted
        conv = modules.messaging.get_conversation(group.id, owner.id)
        assert conv is None


class TestGroupMessages:
    """Tests for messaging in groups."""

    def test_send_message_in_group(self, group_conversation):
        """Test sending message in group."""
        group, owner, member1, member2, messaging = group_conversation

        msg = messaging.send_message(owner.id, group.id, "Group message")

        assert msg.conversation_id == group.id

    def test_all_members_can_send(self, group_conversation):
        """Test that all members can send messages."""
        group, owner, member1, member2, messaging = group_conversation

        msg1 = messaging.send_message(owner.id, group.id, "From owner")
        msg2 = messaging.send_message(member1.id, group.id, "From member1")
        msg3 = messaging.send_message(member2.id, group.id, "From member2")

        assert all(m is not None for m in [msg1, msg2, msg3])

    def test_non_member_cannot_send(self, group_conversation, user_pool):
        """Test that non-members cannot send messages."""
        group, owner, member1, member2, messaging = group_conversation
        non_member = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.send_message(non_member.id, group.id, "Outsider")

    def test_all_members_see_messages(self, group_conversation):
        """Test that all members see messages."""
        group, owner, member1, member2, messaging = group_conversation

        msg = messaging.send_message(owner.id, group.id, "Test")

        msgs_owner = messaging.get_messages(owner.id, group.id)
        msgs_member1 = messaging.get_messages(member1.id, group.id)
        msgs_member2 = messaging.get_messages(member2.id, group.id)

        assert all(
            any(m.id == msg.id for m in msgs)
            for msgs in [msgs_owner, msgs_member1, msgs_member2]
        )


class TestGroupNotifications:
    """Tests for group notification behaviors."""

    def test_member_added_notification(self, group_conversation, user_pool):
        """Test system message when member added."""
        group, owner, member1, member2, messaging = group_conversation
        new_user = user_pool.get_user()

        messaging.add_participant(owner.id, group.id, new_user.id)

        # Check for system message
        messages = messaging.get_messages(owner.id, group.id, limit=5)
        assert any(
            m.message_type.value == "system" and "added" in m.content.lower()
            for m in messages
        )

    def test_member_removed_notification(self, group_conversation):
        """Test system message when member removed."""
        group, owner, member1, member2, messaging = group_conversation

        messaging.remove_participant(owner.id, group.id, member1.id)

        # Check for system message
        messages = messaging.get_messages(owner.id, group.id, limit=5)
        assert any(
            m.message_type.value == "system" and "removed" in m.content.lower()
            for m in messages
        )

    def test_member_left_notification(self, group_conversation):
        """Test system message when member leaves."""
        group, owner, member1, member2, messaging = group_conversation

        messaging.leave_conversation(member1.id, group.id)

        # Check for system message
        messages = messaging.get_messages(owner.id, group.id, limit=5)
        assert any(
            m.message_type.value == "system" and "left" in m.content.lower()
            for m in messages
        )


class TestGroupEdgeCases:
    """Tests for group edge cases."""

    def test_group_with_single_member(self, user_pool, modules):
        """Test group with only owner."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Solo Group")

        assert group.participant_count == 1

    def test_group_at_max_capacity(self, user_pool, modules):
        """Test group at maximum capacity."""
        owner = user_pool.get_user()

        # Create small group
        group = modules.messaging.create_group(
            owner_id=owner.id, name="Full Group", max_participants=3
        )

        # Fill to capacity
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        modules.messaging.add_participant(owner.id, group.id, user1.id)
        modules.messaging.add_participant(owner.id, group.id, user2.id)

        # Try to add one more
        user3 = user_pool.get_user()
        with pytest.raises(ParticipantLimitError):
            modules.messaging.add_participant(owner.id, group.id, user3.id)

    def test_group_duplicate_member_names(self, user_pool, modules):
        """Test group with duplicate initial member IDs."""
        owner = user_pool.get_user()
        member = user_pool.get_user()

        # Try to add same member twice
        group = modules.messaging.create_group(
            owner_id=owner.id, name="Test Group", participant_ids=[member.id, member.id]
        )

        # Should deduplicate
        assert group.participant_count == 2  # owner + member once

    def test_group_owner_in_participant_list(self, user_pool, modules):
        """Test that owner in participant list doesn't duplicate."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(
            owner_id=owner.id,
            name="Test Group",
            participant_ids=[owner.id],  # Owner tries to add self
        )

        assert group.participant_count == 1
