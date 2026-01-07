"""
Participant management tests for messaging module.
"""

import pytest


class TestAddParticipant:
    """Test adding participants to conversations."""

    def test_add_participant_as_owner(self, group_conversation, users):
        """Test owner can add participant."""
        group, user1, user2, user3, messaging = group_conversation
        _, _, _, _ = users

        # Create a new user to add
        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(
            f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!"
        )

        participant = messaging.add_participant(user1.id, group.id, new_user.id)

        assert participant is not None
        assert participant.user_id == new_user.id
        assert participant.role == messaging.ParticipantRole.MEMBER

    def test_add_participant_as_admin(self, group_conversation, users):
        """Test admin can add participant."""
        group, user1, user2, user3, messaging = group_conversation

        # Make user2 admin
        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        # Create new user
        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(
            f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!"
        )

        participant = messaging.add_participant(user2.id, group.id, new_user.id)

        assert participant is not None

    def test_add_participant_as_member_fails(self, group_conversation, users):
        """Test member cannot add participant."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(
            f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!"
        )

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.add_participant(user2.id, group.id, new_user.id)

    def test_add_existing_participant_fails(self, group_conversation):
        """Test adding existing participant fails."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ParticipantExistsError):
            messaging.add_participant(user1.id, group.id, user2.id)

    def test_add_participant_to_dm_fails(self, dm_conversation, users):
        """Test adding participant to DM fails."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users

        with pytest.raises(messaging.ConversationTypeError):
            messaging.add_participant(user1.id, dm.id, user3.id)

    def test_add_participant_at_limit_fails(self, users):
        """Test adding participant when at limit fails."""
        user1, user2, user3, messaging = users

        # Create group with max 2 participants
        group = messaging.create_group(
            owner_id=user1.id,
            name="Tiny Group",
            participant_ids=[user2.id],
            max_participants=2,
        )

        with pytest.raises(messaging.ParticipantLimitError):
            messaging.add_participant(user1.id, group.id, user3.id)

    def test_add_participant_as_owner_fails(self, group_conversation, users):
        """Test cannot add participant as owner."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(
            f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!"
        )

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.add_participant(
                user1.id, group.id, new_user.id, messaging.ParticipantRole.OWNER
            )

    def test_add_participant_sends_system_message(self, group_conversation, users):
        """Test adding participant sends system message."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(
            f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!"
        )

        messaging.add_participant(user1.id, group.id, new_user.id)

        messages = messaging.get_messages(user1.id, group.id)
        system_msgs = [
            m for m in messages if m.message_type == messaging.MessageType.SYSTEM
        ]

        assert any("added" in m.content.lower() for m in system_msgs)


class TestRemoveParticipant:
    """Test removing participants from conversations."""

    def test_remove_participant_as_owner(self, group_conversation):
        """Test owner can remove participant."""
        group, user1, user2, user3, messaging = group_conversation

        result = messaging.remove_participant(user1.id, group.id, user2.id)

        assert result is True

        participants = messaging.get_participants(user1.id, group.id)
        user_ids = [p.user_id for p in participants]
        assert user2.id not in user_ids

    def test_remove_participant_as_admin(self, group_conversation):
        """Test admin can remove member."""
        group, user1, user2, user3, messaging = group_conversation

        # Make user2 admin
        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        result = messaging.remove_participant(user2.id, group.id, user3.id)

        assert result is True

    def test_admin_cannot_remove_owner(self, group_conversation):
        """Test admin cannot remove owner."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.remove_participant(user2.id, group.id, user1.id)

    def test_admin_cannot_remove_admin(self, group_conversation):
        """Test admin cannot remove other admin."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )
        messaging.update_participant_role(
            user1.id, group.id, user3.id, messaging.ParticipantRole.ADMIN
        )

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.remove_participant(user2.id, group.id, user3.id)

    def test_member_cannot_remove(self, group_conversation):
        """Test member cannot remove anyone."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.remove_participant(user2.id, group.id, user3.id)

    def test_owner_cannot_remove_self(self, group_conversation):
        """Test owner cannot remove themselves."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.remove_participant(user1.id, group.id, user1.id)

    def test_remove_nonexistent_participant(self, group_conversation, users):
        """Test removing nonexistent participant fails."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        new_user = auth.register(
            f"new_{unique_id}", f"new_{unique_id}@example.com", "TestPass123!"
        )

        with pytest.raises(messaging.ParticipantNotFoundError):
            messaging.remove_participant(user1.id, group.id, new_user.id)

    def test_remove_from_dm_fails(self, dm_conversation):
        """Test removing from DM fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.ConversationTypeError):
            messaging.remove_participant(user1.id, dm.id, user2.id)

    def test_remove_sends_system_message(self, group_conversation):
        """Test removing participant sends system message."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.remove_participant(user1.id, group.id, user2.id)

        messages = messaging.get_messages(user1.id, group.id)
        system_msgs = [
            m for m in messages if m.message_type == messaging.MessageType.SYSTEM
        ]

        assert any("removed" in m.content.lower() for m in system_msgs)


class TestUpdateParticipantRole:
    """Test updating participant roles."""

    def test_owner_can_promote_to_admin(self, group_conversation):
        """Test owner can promote member to admin."""
        group, user1, user2, user3, messaging = group_conversation

        participant = messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        assert participant.role == messaging.ParticipantRole.ADMIN

    def test_owner_can_demote_admin(self, group_conversation):
        """Test owner can demote admin to member."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        participant = messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.MEMBER
        )

        assert participant.role == messaging.ParticipantRole.MEMBER

    def test_admin_cannot_change_roles(self, group_conversation):
        """Test admin cannot change roles."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.update_participant_role(
                user2.id, group.id, user3.id, messaging.ParticipantRole.ADMIN
            )

    def test_cannot_assign_owner_role(self, group_conversation):
        """Test cannot assign owner role."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.update_participant_role(
                user1.id, group.id, user2.id, messaging.ParticipantRole.OWNER
            )

    def test_cannot_change_own_role(self, group_conversation):
        """Test owner cannot change own role."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.update_participant_role(
                user1.id, group.id, user1.id, messaging.ParticipantRole.ADMIN
            )

    def test_change_role_in_dm_fails(self, dm_conversation):
        """Test changing role in DM fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.ConversationTypeError):
            messaging.update_participant_role(
                user1.id, dm.id, user2.id, messaging.ParticipantRole.ADMIN
            )


class TestGetParticipants:
    """Test getting participants."""

    def test_get_participants_as_member(self, group_conversation):
        """Test member can get participants."""
        group, user1, user2, user3, messaging = group_conversation

        participants = messaging.get_participants(user2.id, group.id)

        assert len(participants) == 3

    def test_get_participants_as_non_member_fails(self, group_conversation, users):
        """Test non-member cannot get participants."""
        group, user1, user2, user3, messaging = group_conversation

        from src.core import auth
        import uuid

        unique_id = uuid.uuid4().hex[:8]
        outsider = auth.register(
            f"out_{unique_id}", f"out_{unique_id}@example.com", "TestPass123!"
        )

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.get_participants(outsider.id, group.id)

    def test_get_participants_includes_roles(self, group_conversation):
        """Test participants include role information."""
        group, user1, user2, user3, messaging = group_conversation

        participants = messaging.get_participants(user1.id, group.id)

        owner = next(p for p in participants if p.user_id == user1.id)
        member = next(p for p in participants if p.user_id == user2.id)

        assert owner.role == messaging.ParticipantRole.OWNER
        assert member.role == messaging.ParticipantRole.MEMBER


class TestMuteConversation:
    """Test muting conversations."""

    def test_mute_conversation(self, dm_conversation):
        """Test muting a conversation."""
        dm, user1, user2, messaging = dm_conversation

        result = messaging.mute_conversation(user1.id, dm.id, muted=True)

        assert result is True

    def test_unmute_conversation(self, dm_conversation):
        """Test unmuting a conversation."""
        dm, user1, user2, messaging = dm_conversation

        messaging.mute_conversation(user1.id, dm.id, muted=True)
        result = messaging.mute_conversation(user1.id, dm.id, muted=False)

        assert result is True

    def test_mute_with_duration(self, dm_conversation):
        """Test muting with duration."""
        dm, user1, user2, messaging = dm_conversation

        import time

        until = int(time.time() * 1000) + 3600000  # 1 hour from now

        result = messaging.mute_conversation(user1.id, dm.id, muted=True, until=until)

        assert result is True

    def test_mute_non_participant_fails(self, dm_conversation, users):
        """Test muting as non-participant fails."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.mute_conversation(user3.id, dm.id, muted=True)
