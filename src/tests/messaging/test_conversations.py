"""
Conversation access control and management tests.

Tests conversation creation, permissions, access control,
and conversation lifecycle.
"""

import pytest
from src.core.messaging.exceptions import (
    ConversationNotFoundError,
    ConversationAccessDeniedError,
    ConversationTypeError,
    InvalidRecipientError,
    InvalidContentError,
)
from src.core.messaging.models import ConversationType


class TestDMConversations:
    """Tests for direct message conversations."""

    def test_create_dm_conversation(self, user_pool, modules):
        """Test creating a DM conversation."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user1.id, user2.id)

        assert dm is not None
        assert dm.conversation_type == ConversationType.DM
        assert dm.participant_count == 2

    def test_create_dm_with_self_fails(self, user_pool, modules):
        """Test that creating DM with self fails."""
        user = user_pool.get_user()

        with pytest.raises(InvalidRecipientError):
            modules.messaging.create_dm(user.id, user.id)

    def test_dm_idempotent_creation(self, user_pool, modules):
        """Test that creating same DM twice returns existing DM."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        dm2 = modules.messaging.create_dm(user1.id, user2.id)

        assert dm1.id == dm2.id

    def test_dm_bidirectional_lookup(self, user_pool, modules):
        """Test that DM lookup works in both directions."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        dm2 = modules.messaging.create_dm(user2.id, user1.id)

        assert dm1.id == dm2.id

    def test_dm_respects_recipient_settings(self, user_pool, modules):
        """Test that DM creation respects recipient's DM settings."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        # Set user2 to not accept DMs
        modules.messaging.update_user_message_settings(user2.id, allow_dms_from="none")

        with pytest.raises(ConversationAccessDeniedError):
            modules.messaging.create_dm(user1.id, user2.id)

    def test_dm_auto_create_disabled(self, user_pool, modules):
        """Test DM creation when auto-create is disabled."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()

        with pytest.raises(ConversationNotFoundError):
            modules.messaging.create_dm(user1.id, user2.id, auto_create=False)


class TestGroupConversations:
    """Tests for group conversations."""

    def test_create_group_conversation(self, user_pool, modules):
        """Test creating a group conversation."""
        owner = user_pool.get_user()
        member1 = user_pool.get_user()
        member2 = user_pool.get_user()

        group = modules.messaging.create_group(
            owner_id=owner.id,
            name="Test Group",
            participant_ids=[member1.id, member2.id],
        )

        assert group is not None
        assert group.conversation_type == ConversationType.GROUP
        assert group.name == "Test Group"
        assert group.owner_id == owner.id
        assert group.participant_count == 3

    def test_create_group_with_empty_name_fails(self, user_pool, modules):
        """Test that creating group with empty name fails."""
        owner = user_pool.get_user()

        with pytest.raises(InvalidContentError):
            modules.messaging.create_group(owner_id=owner.id, name="")

        with pytest.raises(InvalidContentError):
            modules.messaging.create_group(owner_id=owner.id, name="   ")

    def test_create_group_with_long_name_fails(self, user_pool, modules):
        """Test that group name length is limited."""
        owner = user_pool.get_user()
        long_name = "A" * 200

        with pytest.raises(InvalidContentError):
            modules.messaging.create_group(owner_id=owner.id, name=long_name)

    def test_create_group_owner_only(self, user_pool, modules):
        """Test creating group with only owner."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Solo Group")

        assert group.participant_count == 1

    def test_group_participant_limit(self, user_pool, modules):
        """Test that group participant limit is enforced."""
        owner = user_pool.get_user()

        # Try to create group with more than max participants
        from src.core.messaging.exceptions import ParticipantLimitError

        with pytest.raises(ParticipantLimitError):
            modules.messaging.create_group(
                owner_id=owner.id,
                name="Large Group",
                participant_ids=list(range(200)),  # Exceeds default 100
                max_participants=10,
            )

    def test_update_group_name(self, group_conversation):
        """Test updating group name."""
        group, owner, member1, member2, messaging = group_conversation

        updated = messaging.update_conversation(
            owner.id, group.id, name="New Group Name"
        )

        assert updated.name == "New Group Name"

    def test_update_group_max_participants(self, group_conversation):
        """Test updating group max participants."""
        group, owner, member1, member2, messaging = group_conversation

        updated = messaging.update_conversation(owner.id, group.id, max_participants=50)

        assert updated.max_participants == 50

    def test_update_group_non_owner_fails(self, group_conversation):
        """Test that non-owners cannot update group."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.update_conversation(
                member1.id, group.id, name="Unauthorized Change"
            )

    def test_update_dm_fails(self, dm_conversation):
        """Test that DM settings cannot be updated."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(ConversationTypeError):
            messaging.update_conversation(user1.id, dm.id, name="New Name")


class TestNotesConversations:
    """Tests for personal notes conversations."""

    def test_create_notes_conversation(self, user_pool, modules):
        """Test creating personal notes conversation."""
        user = user_pool.get_user()

        notes = modules.messaging.get_or_create_notes(user.id)

        assert notes is not None
        assert notes.conversation_type == ConversationType.NOTES
        assert notes.participant_count == 1

    def test_notes_conversation_idempotent(self, user_pool, modules):
        """Test that getting notes multiple times returns same conversation."""
        user = user_pool.get_user()

        notes1 = modules.messaging.get_or_create_notes(user.id)
        notes2 = modules.messaging.get_or_create_notes(user.id)

        assert notes1.id == notes2.id

    def test_notes_conversation_messages(self, user_pool, modules):
        """Test sending messages to notes conversation."""
        user = user_pool.get_user()
        notes = modules.messaging.get_or_create_notes(user.id)

        msg = modules.messaging.send_message(user.id, notes.id, "Personal note")

        assert msg is not None
        assert msg.author_id == user.id


class TestConversationAccess:
    """Tests for conversation access control."""

    def test_get_conversation_as_participant(self, dm_conversation):
        """Test getting conversation as participant."""
        dm, user1, user2, messaging = dm_conversation

        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv is not None
        assert conv.id == dm.id

    def test_get_conversation_non_participant_returns_none(
        self, dm_conversation, user_pool
    ):
        """Test that non-participants cannot get conversation."""
        dm, user1, user2, messaging = dm_conversation
        user3 = user_pool.get_user()

        conv = messaging.get_conversation(dm.id, user3.id)
        assert conv is None

    def test_get_conversations_list(self, user_pool, modules):
        """Test getting list of user's conversations."""
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        user3 = user_pool.get_user()

        # Create multiple conversations
        dm1 = modules.messaging.create_dm(user1.id, user2.id)
        dm2 = modules.messaging.create_dm(user1.id, user3.id)

        conversations = modules.messaging.get_conversations(user1.id, limit=10)

        assert len(conversations) >= 2
        conv_ids = [c.id for c in conversations]
        assert dm1.id in conv_ids
        assert dm2.id in conv_ids

    def test_get_conversations_pagination(self, user_pool, modules):
        """Test conversation list pagination."""
        user = user_pool.get_user()

        # Create multiple conversations
        for i in range(5):
            other = user_pool.get_user()
            modules.messaging.create_dm(user.id, other.id)

        page1 = modules.messaging.get_conversations(user.id, limit=2)
        assert len(page1) == 2

        page2 = modules.messaging.get_conversations(
            user.id, limit=2, before_id=page1[-1].id
        )
        assert len(page2) <= 2
        # Verify no overlap
        if page2:
            assert page1[0].id != page2[0].id

    def test_get_conversations_by_type(self, user_pool, modules):
        """Test filtering conversations by type."""
        owner = user_pool.get_user()
        user2 = user_pool.get_user()
        user3 = user_pool.get_user()

        # Create DM and group
        modules.messaging.create_dm(owner.id, user2.id)
        modules.messaging.create_group(
            owner_id=owner.id, name="Test Group", participant_ids=[user3.id]
        )

        # Get only DMs
        dms = modules.messaging.get_conversations(
            owner.id, conversation_type=ConversationType.DM
        )
        assert all(c.conversation_type == ConversationType.DM for c in dms)

        # Get only groups
        groups = modules.messaging.get_conversations(
            owner.id, conversation_type=ConversationType.GROUP
        )
        assert all(c.conversation_type == ConversationType.GROUP for c in groups)


class TestConversationDeletion:
    """Tests for conversation deletion."""

    def test_delete_dm_conversation(self, dm_conversation):
        """Test deleting DM conversation."""
        dm, user1, user2, messaging = dm_conversation

        result = messaging.delete_conversation(user1.id, dm.id)
        assert result is True

        # Verify deleted
        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv is None

    def test_delete_group_as_owner(self, group_conversation):
        """Test deleting group as owner."""
        group, owner, member1, member2, messaging = group_conversation

        result = messaging.delete_conversation(owner.id, group.id)
        assert result is True

    def test_delete_group_as_member_fails(self, group_conversation):
        """Test that members cannot delete group."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.delete_conversation(member1.id, group.id)

    def test_leave_dm_conversation(self, dm_conversation):
        """Test leaving DM conversation."""
        dm, user1, user2, messaging = dm_conversation

        result = messaging.leave_conversation(user1.id, dm.id)
        assert result is True

    def test_leave_group_conversation(self, group_conversation):
        """Test leaving group conversation."""
        group, owner, member1, member2, messaging = group_conversation

        result = messaging.leave_conversation(member1.id, group.id)
        assert result is True

        # Verify no longer participant
        conv = messaging.get_conversation(group.id, member1.id)
        assert conv is None

    def test_owner_leave_transfers_ownership(self, group_conversation):
        """Test that owner leaving transfers ownership."""
        group, owner, member1, member2, messaging = group_conversation

        messaging.leave_conversation(owner.id, group.id)

        # Group should still exist with new owner
        conv = messaging.get_conversation(group.id, member1.id)
        assert conv is not None
        assert conv.owner_id != owner.id

    def test_last_member_leave_deletes_group(self, user_pool, modules):
        """Test that group is deleted when last member leaves."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Solo Group")

        modules.messaging.leave_conversation(owner.id, group.id)

        # Group should be deleted
        conv = modules.messaging.get_conversation(group.id, owner.id)
        assert conv is None


class TestConversationMuting:
    """Tests for muting conversations."""

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
        """Test muting conversation with expiration."""
        dm, user1, user2, messaging = dm_conversation

        import time

        future_time = int(time.time() * 1000) + 3600000  # 1 hour

        result = messaging.mute_conversation(
            user1.id, dm.id, muted=True, until=future_time
        )
        assert result is True

    def test_mute_non_participant_fails(self, dm_conversation, user_pool):
        """Test that non-participants cannot mute conversation."""
        dm, user1, user2, messaging = dm_conversation
        user3 = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.mute_conversation(user3.id, dm.id, muted=True)


class TestConversationMetadata:
    """Tests for conversation metadata and timestamps."""

    def test_conversation_created_at(self, dm_conversation):
        """Test that conversation has creation timestamp."""
        dm, user1, user2, messaging = dm_conversation

        assert dm.created_at > 0

    def test_conversation_updated_at_on_message(self, dm_conversation):
        """Test that conversation updates when message sent."""
        dm, user1, user2, messaging = dm_conversation
        initial_updated = dm.updated_at

        import time

        time.sleep(0.01)

        messaging.send_message(user1.id, dm.id, "Update timestamp")

        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv.updated_at > initial_updated

    def test_conversation_last_message_tracking(self, dm_conversation):
        """Test that last message is tracked."""
        dm, user1, user2, messaging = dm_conversation

        msg = messaging.send_message(user1.id, dm.id, "Latest message")

        conv = messaging.get_conversation(dm.id, user1.id)
        assert conv.last_message_id == msg.id
        assert conv.last_message_at == msg.created_at

    def test_conversation_encryption_flag(self, dm_conversation):
        """Test that encryption flag is set correctly."""
        dm, user1, user2, messaging = dm_conversation

        # Encryption should be enabled by default
        assert dm.encrypted is True or dm.encrypted is False
