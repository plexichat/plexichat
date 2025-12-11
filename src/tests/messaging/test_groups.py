"""
Group conversation tests for messaging module.
"""

import pytest


class TestGroupCreation:
    """Test group-specific creation behavior."""

    def test_group_has_name(self, group_conversation):
        """Test group has a name."""
        group, user1, user2, user3, messaging = group_conversation

        assert group.name.startswith("Test Group")

    def test_group_has_owner(self, group_conversation):
        """Test group has an owner."""
        group, user1, user2, user3, messaging = group_conversation

        assert group.owner_id == user1.id

    def test_group_owner_has_owner_role(self, group_conversation):
        """Test group owner has owner role."""
        group, user1, user2, user3, messaging = group_conversation

        participants = messaging.get_participants(user1.id, group.id)
        owner = next(p for p in participants if p.user_id == user1.id)

        assert owner.role == messaging.ParticipantRole.OWNER

    def test_group_members_have_member_role(self, group_conversation):
        """Test group members have member role."""
        group, user1, user2, user3, messaging = group_conversation

        participants = messaging.get_participants(user1.id, group.id)
        member = next(p for p in participants if p.user_id == user2.id)

        assert member.role == messaging.ParticipantRole.MEMBER

    def test_group_default_max_participants(self, users):
        """Test group has default max participants."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Test Group")

        assert group.max_participants == 100

    def test_group_custom_max_participants(self, users):
        """Test group can have custom max participants."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Small Group", max_participants=10)

        assert group.max_participants == 10

    def test_group_creation_sends_system_message(self, users):
        """Test group creation sends system message."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "New Group")

        messages = messaging.get_messages(user1.id, group.id)
        system_msgs = [m for m in messages if m.message_type == messaging.MessageType.SYSTEM]

        assert len(system_msgs) >= 1
        assert "created" in system_msgs[0].content.lower()


class TestGroupParticipants:
    """Test group participant management."""

    def test_add_participant_to_group(self, group_conversation, users):
        """Test adding participant to group."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!")

        participant = messaging.add_participant(user1.id, group.id, new_user.id)

        assert participant is not None
        assert participant.user_id == new_user.id

    def test_add_participant_as_admin(self, group_conversation, users):
        """Test adding participant with admin role."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!")

        participant = messaging.add_participant(
            user1.id, group.id, new_user.id, messaging.ParticipantRole.ADMIN
        )

        assert participant.role == messaging.ParticipantRole.ADMIN

    def test_remove_participant_from_group(self, group_conversation):
        """Test removing participant from group."""
        group, user1, user2, user3, messaging = group_conversation

        result = messaging.remove_participant(user1.id, group.id, user2.id)

        assert result is True

        participants = messaging.get_participants(user1.id, group.id)
        user_ids = [p.user_id for p in participants]
        assert user2.id not in user_ids

    def test_participant_limit_enforced(self, users):
        """Test participant limit is enforced."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(
            user1.id, "Tiny Group",
            participant_ids=[user2.id],
            max_participants=2
        )

        with pytest.raises(messaging.ParticipantLimitError):
            messaging.add_participant(user1.id, group.id, user3.id)


class TestGroupRoles:
    """Test group role management."""

    def test_promote_to_admin(self, group_conversation):
        """Test promoting member to admin."""
        group, user1, user2, user3, messaging = group_conversation

        participant = messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        assert participant.role == messaging.ParticipantRole.ADMIN

    def test_demote_admin_to_member(self, group_conversation):
        """Test demoting admin to member."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN)

        participant = messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.MEMBER
        )

        assert participant.role == messaging.ParticipantRole.MEMBER

    def test_admin_can_add_members(self, group_conversation, users):
        """Test admin can add members."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN)

        from src.core import auth
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!")

        participant = messaging.add_participant(user2.id, group.id, new_user.id)

        assert participant is not None

    def test_admin_can_remove_members(self, group_conversation):
        """Test admin can remove members."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN)

        result = messaging.remove_participant(user2.id, group.id, user3.id)

        assert result is True

    def test_member_cannot_add(self, group_conversation, users):
        """Test member cannot add participants."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!")

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.add_participant(user2.id, group.id, new_user.id)

    def test_member_cannot_remove(self, group_conversation):
        """Test member cannot remove participants."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.remove_participant(user2.id, group.id, user3.id)


class TestGroupOwnership:
    """Test group ownership behavior."""

    def test_owner_can_delete_group(self, group_conversation):
        """Test owner can delete group."""
        group, user1, user2, user3, messaging = group_conversation

        result = messaging.delete_conversation(user1.id, group.id)

        assert result is True

    def test_non_owner_cannot_delete_group(self, group_conversation):
        """Test non-owner cannot delete group."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.delete_conversation(user2.id, group.id)

    def test_owner_leaving_transfers_ownership(self, group_conversation):
        """Test owner leaving transfers ownership."""
        group, user1, user2, user3, messaging = group_conversation

        # Make user2 admin first
        messaging.update_participant_role(user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN)

        messaging.leave_conversation(user1.id, group.id)

        updated_group = messaging.get_conversation(group.id, user2.id)
        assert updated_group.owner_id == user2.id

    def test_last_member_leaving_deletes_group(self, users):
        """Test last member leaving deletes group."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(user1.id, "Solo Group")

        messaging.leave_conversation(user1.id, group.id)

        # Group should be deleted
        assert messaging.get_conversation(group.id, user1.id) is None


class TestGroupSettings:
    """Test group settings management."""

    def test_update_group_name(self, group_conversation):
        """Test updating group name."""
        group, user1, user2, user3, messaging = group_conversation

        updated = messaging.update_conversation(user1.id, group.id, name="New Name")

        assert updated.name == "New Name"

    def test_update_max_participants(self, group_conversation):
        """Test updating max participants."""
        group, user1, user2, user3, messaging = group_conversation

        updated = messaging.update_conversation(user1.id, group.id, max_participants=50)

        assert updated.max_participants == 50

    def test_admin_can_update_settings(self, group_conversation):
        """Test admin can update group settings."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN)

        updated = messaging.update_conversation(user2.id, group.id, name="Admin Updated")

        assert updated.name == "Admin Updated"

    def test_member_cannot_update_settings(self, group_conversation):
        """Test member cannot update group settings."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.update_conversation(user2.id, group.id, name="Member Updated")


class TestGroupMessaging:
    """Test messaging in groups."""

    def test_all_members_can_send(self, group_conversation):
        """Test all members can send messages."""
        group, user1, user2, user3, messaging = group_conversation

        msg1 = messaging.send_message(user1.id, group.id, "From owner")
        msg2 = messaging.send_message(user2.id, group.id, "From member")
        msg3 = messaging.send_message(user3.id, group.id, "From another member")

        assert msg1 is not None
        assert msg2 is not None
        assert msg3 is not None

    def test_all_members_can_read(self, group_conversation):
        """Test all members can read messages."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.send_message(user1.id, group.id, "Test message")

        msgs1 = messaging.get_messages(user1.id, group.id)
        msgs2 = messaging.get_messages(user2.id, group.id)
        msgs3 = messaging.get_messages(user3.id, group.id)

        # All should see at least the system message and test message
        assert len(msgs1) >= 1
        assert len(msgs2) >= 1
        assert len(msgs3) >= 1

    def test_non_member_cannot_send(self, group_conversation, users):
        """Test non-member cannot send messages."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        outsider = auth.register(f"out_{unique_id}", f"out_{unique_id}@example.com", "TestPass123!")

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.send_message(outsider.id, group.id, "Hello")

    def test_non_member_cannot_read(self, group_conversation, users):
        """Test non-member cannot read messages."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid
        unique_id = uuid.uuid4().hex[:8]
        outsider = auth.register(f"out_{unique_id}", f"out_{unique_id}@example.com", "TestPass123!")

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.get_messages(outsider.id, group.id)

    def test_owner_can_delete_any_message(self, group_conversation):
        """Test owner can delete any message."""
        group, user1, user2, user3, messaging = group_conversation

        msg = messaging.send_message(user2.id, group.id, "Member message")

        result = messaging.delete_message(user1.id, msg.id)

        assert result is True

    def test_admin_can_delete_member_message(self, group_conversation):
        """Test admin can delete member messages."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN)

        msg = messaging.send_message(user3.id, group.id, "Member message")

        result = messaging.delete_message(user2.id, msg.id)

        assert result is True

    def test_member_cannot_delete_others_message(self, group_conversation):
        """Test member cannot delete others' messages."""
        group, user1, user2, user3, messaging = group_conversation

        msg = messaging.send_message(user1.id, group.id, "Owner message")

        with pytest.raises(messaging.MessageAccessDeniedError):
            messaging.delete_message(user2.id, msg.id)
