"""Tests for messaging participant management."""

import pytest

from src.core.messaging.exceptions import (
    ConversationTypeError,
    ConversationAccessDeniedError,
)


@pytest.mark.messaging
class TestParticipants:
    """Tests for participant management in conversations."""

    def test_dm_has_two_participants(self, messaging_manager, two_users):
        """Test that DM conversations have exactly two participants."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        participants = messaging_manager.get_participants(user1.id, dm.id)
        assert len(participants) == 2

    def test_group_has_all_participants(self, messaging_manager, three_users):
        """Test that group includes all specified participants."""
        owner, member1, member2 = three_users
        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )
        participants = messaging_manager.get_participants(owner.id, group.id)
        participant_ids = {p.user_id for p in participants}
        assert owner.id in participant_ids
        assert member1.id in participant_ids
        assert member2.id in participant_ids

    def test_add_participant_to_group(self, messaging_manager, auth_manager):
        """Test adding a participant to a group conversation."""
        from unittest.mock import patch
        from src.utils import encryption
        import uuid

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            owner = auth_manager.register(
                f"owner_{uuid.uuid4().hex[:6]}", "owner@test.com", "TestPass123!"
            )
            member1 = auth_manager.register(
                f"mem1_{uuid.uuid4().hex[:6]}", "mem1@test.com", "TestPass123!"
            )
            member2 = auth_manager.register(
                f"mem2_{uuid.uuid4().hex[:6]}", "mem2@test.com", "TestPass123!"
            )

        group = messaging_manager.create_group(owner.id, "Test Group", [member1.id])
        participant = messaging_manager.add_participant(owner.id, group.id, member2.id)
        assert participant.user_id == member2.id

    def test_cannot_add_participant_to_dm(self, messaging_manager, two_users):
        """Test that participants cannot be added to DM conversations."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        with pytest.raises((ConversationTypeError, ConversationAccessDeniedError)):
            messaging_manager.add_participant(user1.id, dm.id, 99999)

    def test_is_participant_check(self, messaging_manager, two_users):
        """Test the is_participant check method."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        assert messaging_manager.is_participant(dm.id, user1.id) is True
        assert messaging_manager.is_participant(dm.id, 99999) is False

    def test_remove_participant_from_group(self, messaging_manager, three_users):
        """Test removing a participant from a group."""
        owner, member1, member2 = three_users
        group = messaging_manager.create_group(
            owner.id, "Test Group", [member1.id, member2.id]
        )
        result = messaging_manager.remove_participant(owner.id, group.id, member1.id)
        assert result is True

    def test_mute_conversation(self, messaging_manager, two_users):
        """Test muting a conversation."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        result = messaging_manager.mute_conversation(user1.id, dm.id, muted=True)
        assert result is True

    def test_unmute_conversation(self, messaging_manager, two_users):
        """Test unmuting a conversation."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.mute_conversation(user1.id, dm.id, muted=True)
        result = messaging_manager.mute_conversation(user1.id, dm.id, muted=False)
        assert result is True
