"""
Conversation tests for messaging module.
"""

import pytest


class TestCreateDM:
    """Test DM conversation creation."""

    def test_create_dm_success(self, users):
        """Test successful DM creation."""
        user1, user2, user3, messaging = users

        dm = messaging.create_dm(user1.id, user2.id)

        assert dm is not None
        assert dm.conversation_type == messaging.ConversationType.DM
        assert dm.max_participants == 2

    def test_create_dm_returns_existing(self, users):
        """Test that creating DM returns existing conversation."""
        user1, user2, user3, messaging = users

        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user1.id, user2.id)

        assert dm1.id == dm2.id

    def test_create_dm_symmetric(self, users):
        """Test that DM is same regardless of who initiates."""
        user1, user2, user3, messaging = users

        dm1 = messaging.create_dm(user1.id, user2.id)
        dm2 = messaging.create_dm(user2.id, user1.id)

        assert dm1.id == dm2.id

    def test_create_dm_with_self_fails(self, users):
        """Test that creating DM with self fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.MessagingError):
            messaging.create_dm(user1.id, user1.id)

    def test_create_dm_respects_recipient_settings(self, users):
        """Test that DM creation respects recipient's DM settings."""
        user1, user2, user3, messaging = users

        # Disable DMs for user2
        messaging.update_user_message_settings(user2.id, allow_dms_from="none")

        try:
            with pytest.raises(messaging.ConversationAccessDeniedError):
                messaging.create_dm(user1.id, user2.id)
        finally:
            # Reset settings for other tests
            messaging.update_user_message_settings(user2.id, allow_dms_from="everyone")


class TestCreateGroup:
    """Test group conversation creation."""

    def test_create_group_success(self, users):
        """Test successful group creation."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(
            owner_id=user1.id, name="Test Group", participant_ids=[user2.id]
        )

        assert group is not None
        assert group.conversation_type == messaging.ConversationType.GROUP
        assert group.name == "Test Group"
        assert group.owner_id == user1.id

    def test_create_group_owner_auto_added(self, users):
        """Test that owner is automatically added as participant."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(owner_id=user1.id, name="Test Group")

        participants = messaging.get_participants(user1.id, group.id)
        user_ids = [p.user_id for p in participants]

        assert user1.id in user_ids

    def test_create_group_with_participants(self, users):
        """Test group creation with initial participants."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(
            owner_id=user1.id, name="Test Group", participant_ids=[user2.id, user3.id]
        )

        participants = messaging.get_participants(user1.id, group.id)
        assert len(participants) == 3

    def test_create_group_empty_name_fails(self, users):
        """Test that empty group name fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.InvalidContentError):
            messaging.create_group(owner_id=user1.id, name="")

    def test_create_group_whitespace_name_fails(self, users):
        """Test that whitespace-only group name fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.InvalidContentError):
            messaging.create_group(owner_id=user1.id, name="   ")

    def test_create_group_long_name_fails(self, users):
        """Test that overly long group name fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.InvalidContentError):
            messaging.create_group(owner_id=user1.id, name="x" * 101)

    def test_create_group_custom_max_participants(self, users):
        """Test group creation with custom max participants."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(
            owner_id=user1.id, name="Small Group", max_participants=5
        )

        assert group.max_participants == 5

    def test_create_group_too_many_initial_participants(self, users):
        """Test that exceeding max participants on creation fails."""
        user1, user2, user3, messaging = users

        with pytest.raises(messaging.ParticipantLimitError):
            messaging.create_group(
                owner_id=user1.id,
                name="Tiny Group",
                participant_ids=[user2.id, user3.id],
                max_participants=2,
            )

    def test_create_group_sends_system_message(self, users):
        """Test that group creation sends system message."""
        user1, user2, user3, messaging = users

        group = messaging.create_group(owner_id=user1.id, name="Test Group")

        messages = messaging.get_messages(user1.id, group.id)
        assert len(messages) == 1
        assert messages[0].message_type == messaging.MessageType.SYSTEM


class TestGetConversation:
    """Test getting conversations."""

    def test_get_conversation_as_participant(self, dm_conversation):
        """Test getting conversation as participant."""
        dm, user1, user2, messaging = dm_conversation

        conv = messaging.get_conversation(dm.id, user1.id)

        assert conv is not None
        assert conv.id == dm.id

    def test_get_conversation_as_non_participant(self, dm_conversation, users):
        """Test getting conversation as non-participant returns None."""
        dm, user1, user2, messaging = dm_conversation
        _, _, user3, _ = users

        conv = messaging.get_conversation(dm.id, user3.id)

        assert conv is None

    def test_get_conversation_nonexistent(self, users):
        """Test getting nonexistent conversation returns None."""
        user1, user2, user3, messaging = users

        conv = messaging.get_conversation(999999999, user1.id)

        assert conv is None


class TestGetConversations:
    """Test listing conversations."""

    def test_get_conversations_returns_user_conversations(self, users):
        """Test that get_conversations returns user's conversations."""
        user1, user2, user3, messaging = users

        dm = messaging.create_dm(user1.id, user2.id)
        group = messaging.create_group(user1.id, "Test Group")

        convs = messaging.get_conversations(user1.id)
        conv_ids = [c.id for c in convs]

        assert dm.id in conv_ids
        assert group.id in conv_ids

    def test_get_conversations_excludes_others(self, users):
        """Test that get_conversations excludes other users' conversations."""
        user1, user2, user3, messaging = users

        dm = messaging.create_dm(user1.id, user2.id)

        convs = messaging.get_conversations(user3.id)
        conv_ids = [c.id for c in convs]

        assert dm.id not in conv_ids

    def test_get_conversations_filter_by_type(self, users):
        """Test filtering conversations by type."""
        user1, user2, user3, messaging = users

        dm = messaging.create_dm(user1.id, user2.id)
        group = messaging.create_group(user1.id, "Test Group")

        dms = messaging.get_conversations(
            user1.id, conversation_type=messaging.ConversationType.DM
        )
        groups = messaging.get_conversations(
            user1.id, conversation_type=messaging.ConversationType.GROUP
        )

        dm_ids = [c.id for c in dms]
        group_ids = [c.id for c in groups]

        assert dm.id in dm_ids
        assert group.id not in dm_ids
        assert group.id in group_ids
        assert dm.id not in group_ids

    def test_get_conversations_pagination(self, users):
        """Test conversation pagination."""
        user1, user2, user3, messaging = users

        # Create multiple groups
        groups = []
        for i in range(5):
            g = messaging.create_group(user1.id, f"Group {i}")
            groups.append(g)

        # Get first page
        page1 = messaging.get_conversations(user1.id, limit=2)
        assert len(page1) == 2

        # Get second page
        page2 = messaging.get_conversations(user1.id, limit=2, before_id=page1[-1].id)
        assert len(page2) >= 1

        # Ensure no overlap
        page1_ids = [c.id for c in page1]
        page2_ids = [c.id for c in page2]
        assert not set(page1_ids) & set(page2_ids)


class TestUpdateConversation:
    """Test updating conversations."""

    def test_update_group_name(self, group_conversation):
        """Test updating group name."""
        group, user1, user2, user3, messaging = group_conversation

        updated = messaging.update_conversation(
            user_id=user1.id, conversation_id=group.id, name="New Name"
        )

        assert updated.name == "New Name"

    def test_update_group_max_participants(self, group_conversation):
        """Test updating group max participants."""
        group, user1, user2, user3, messaging = group_conversation

        updated = messaging.update_conversation(
            user_id=user1.id, conversation_id=group.id, max_participants=50
        )

        assert updated.max_participants == 50

    def test_update_dm_fails(self, dm_conversation):
        """Test that updating DM fails."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(messaging.ConversationTypeError):
            messaging.update_conversation(user1.id, dm.id, name="New Name")

    def test_update_by_non_admin_fails(self, group_conversation):
        """Test that non-admin cannot update group."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.update_conversation(user2.id, group.id, name="New Name")

    def test_update_max_below_current_fails(self, group_conversation):
        """Test that setting max below current count fails."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ParticipantLimitError):
            messaging.update_conversation(user1.id, group.id, max_participants=1)


class TestDeleteConversation:
    """Test deleting conversations."""

    def test_delete_dm_by_participant(self, dm_conversation):
        """Test deleting DM by participant."""
        dm, user1, user2, messaging = dm_conversation

        result = messaging.delete_conversation(user1.id, dm.id)

        assert result is True
        assert messaging.get_conversation(dm.id, user1.id) is None

    def test_delete_group_by_owner(self, group_conversation):
        """Test deleting group by owner."""
        group, user1, user2, user3, messaging = group_conversation

        result = messaging.delete_conversation(user1.id, group.id)

        assert result is True

    def test_delete_group_by_non_owner_fails(self, group_conversation):
        """Test that non-owner cannot delete group."""
        group, user1, user2, user3, messaging = group_conversation

        with pytest.raises(messaging.ConversationAccessDeniedError):
            messaging.delete_conversation(user2.id, group.id)


class TestLeaveConversation:
    """Test leaving conversations."""

    def test_leave_group(self, group_conversation):
        """Test leaving a group."""
        group, user1, user2, user3, messaging = group_conversation

        result = messaging.leave_conversation(user2.id, group.id)

        assert result is True

        participants = messaging.get_participants(user1.id, group.id)
        user_ids = [p.user_id for p in participants]
        assert user2.id not in user_ids

    def test_leave_group_transfers_ownership(self, group_conversation):
        """Test that owner leaving transfers ownership."""
        group, user1, user2, user3, messaging = group_conversation

        # Make user2 admin first
        messaging.update_participant_role(
            user1.id, group.id, user2.id, messaging.ParticipantRole.ADMIN
        )

        messaging.leave_conversation(user1.id, group.id)

        # Check new owner
        updated_group = messaging.get_conversation(group.id, user2.id)
        assert updated_group.owner_id == user2.id

    def test_leave_dm_deletes_conversation(self, dm_conversation):
        """Test that leaving DM deletes it."""
        dm, user1, user2, messaging = dm_conversation

        messaging.leave_conversation(user1.id, dm.id)

        assert messaging.get_conversation(dm.id, user2.id) is None

    def test_leave_sends_system_message(self, group_conversation):
        """Test that leaving sends system message."""
        group, user1, user2, user3, messaging = group_conversation

        messaging.leave_conversation(user2.id, group.id)

        messages = messaging.get_messages(user1.id, group.id)
        system_msgs = [
            m for m in messages if m.message_type == messaging.MessageType.SYSTEM
        ]

        assert any("left" in m.content.lower() for m in system_msgs)
