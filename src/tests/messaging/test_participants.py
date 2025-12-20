"""
Participant manipulation and role management tests.

Tests adding/removing participants, role changes, permissions,
and participant limits.
"""

import pytest
from src.core.messaging.exceptions import (
    ParticipantExistsError,
    ParticipantNotFoundError,
    ParticipantLimitError,
    ConversationAccessDeniedError,
    ConversationTypeError,
)
from src.core.messaging.models import ParticipantRole


class TestParticipantAddition:
    """Tests for adding participants."""

    def test_add_participant_to_group(self, group_conversation, user_pool):
        """Test adding a new participant to group."""
        group, owner, member1, member2, messaging = group_conversation
        new_user = user_pool.get_user()

        participant = messaging.add_participant(owner.id, group.id, new_user.id)

        assert participant.user_id == new_user.id
        assert participant.role == ParticipantRole.MEMBER

    def test_add_participant_as_admin(self, group_conversation, user_pool):
        """Test adding participant with admin role."""
        group, owner, member1, member2, messaging = group_conversation
        new_user = user_pool.get_user()

        # First make member1 an admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        # Admin can add participants
        participant = messaging.add_participant(member1.id, group.id, new_user.id)

        assert participant.user_id == new_user.id

    def test_add_participant_as_member_fails(self, group_conversation, user_pool):
        """Test that regular members cannot add participants."""
        group, owner, member1, member2, messaging = group_conversation
        new_user = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.add_participant(member1.id, group.id, new_user.id)

    def test_add_existing_participant_fails(self, group_conversation):
        """Test that adding existing participant fails."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ParticipantExistsError):
            messaging.add_participant(owner.id, group.id, member1.id)

    def test_add_participant_to_dm_fails(self, dm_conversation, user_pool):
        """Test that participants cannot be added to DM."""
        dm, user1, user2, messaging = dm_conversation
        user3 = user_pool.get_user()

        with pytest.raises(ConversationTypeError):
            messaging.add_participant(user1.id, dm.id, user3.id)

    def test_add_participant_exceeds_limit(self, user_pool, modules):
        """Test that adding participant respects limit."""
        owner = user_pool.get_user()

        # Create group with limit of 3
        group = modules.messaging.create_group(
            owner_id=owner.id, name="Limited Group", max_participants=3
        )

        # Add 2 members (total 3 with owner)
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        modules.messaging.add_participant(owner.id, group.id, user1.id)
        modules.messaging.add_participant(owner.id, group.id, user2.id)

        # Try to add 4th
        user3 = user_pool.get_user()
        with pytest.raises(ParticipantLimitError):
            modules.messaging.add_participant(owner.id, group.id, user3.id)

    def test_add_participant_as_owner_fails(self, group_conversation, user_pool):
        """Test that participants cannot be added as owner."""
        group, owner, member1, member2, messaging = group_conversation
        new_user = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.add_participant(
                owner.id, group.id, new_user.id, role=ParticipantRole.OWNER
            )


class TestParticipantRemoval:
    """Tests for removing participants."""

    def test_remove_participant_as_owner(self, group_conversation):
        """Test removing participant as owner."""
        group, owner, member1, member2, messaging = group_conversation

        result = messaging.remove_participant(owner.id, group.id, member1.id)
        assert result is True

        # Verify removed
        conv = messaging.get_conversation(group.id, member1.id)
        assert conv is None

    def test_remove_participant_as_admin(self, group_conversation):
        """Test that admin can remove members."""
        group, owner, member1, member2, messaging = group_conversation

        # Make member1 admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        # Admin can remove member
        result = messaging.remove_participant(member1.id, group.id, member2.id)
        assert result is True

    def test_remove_participant_as_member_fails(self, group_conversation):
        """Test that members cannot remove participants."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.remove_participant(member1.id, group.id, member2.id)

    def test_admin_cannot_remove_owner(self, group_conversation):
        """Test that admin cannot remove owner."""
        group, owner, member1, member2, messaging = group_conversation

        # Make member1 admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging.remove_participant(member1.id, group.id, owner.id)

    def test_admin_cannot_remove_other_admin(self, group_conversation):
        """Test that admin cannot remove another admin."""
        group, owner, member1, member2, messaging = group_conversation

        # Make both member1 and member2 admins
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )
        messaging.update_participant_role(
            owner.id, group.id, member2.id, ParticipantRole.ADMIN
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging.remove_participant(member1.id, group.id, member2.id)

    def test_owner_cannot_remove_self(self, group_conversation):
        """Test that owner cannot remove themselves."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.remove_participant(owner.id, group.id, owner.id)

    def test_remove_participant_from_dm_fails(self, dm_conversation):
        """Test that participants cannot be removed from DM."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(ConversationTypeError):
            messaging.remove_participant(user1.id, dm.id, user2.id)

    def test_remove_nonexistent_participant_fails(self, group_conversation, user_pool):
        """Test that removing non-participant fails."""
        group, owner, member1, member2, messaging = group_conversation
        non_member = user_pool.get_user()

        with pytest.raises(ParticipantNotFoundError):
            messaging.remove_participant(owner.id, group.id, non_member.id)


class TestParticipantRoles:
    """Tests for participant role management."""

    def test_update_participant_role_to_admin(self, group_conversation):
        """Test promoting participant to admin."""
        group, owner, member1, member2, messaging = group_conversation

        participant = messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        assert participant.role == ParticipantRole.ADMIN

    def test_update_participant_role_to_member(self, group_conversation):
        """Test demoting admin to member."""
        group, owner, member1, member2, messaging = group_conversation

        # First promote to admin
        messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.ADMIN
        )

        # Then demote back to member
        participant = messaging.update_participant_role(
            owner.id, group.id, member1.id, ParticipantRole.MEMBER
        )

        assert participant.role == ParticipantRole.MEMBER

    def test_only_owner_can_change_roles(self, group_conversation):
        """Test that only owner can change roles."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.update_participant_role(
                member1.id, group.id, member2.id, ParticipantRole.ADMIN
            )

    def test_owner_cannot_change_own_role(self, group_conversation):
        """Test that owner cannot change their own role."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.update_participant_role(
                owner.id, group.id, owner.id, ParticipantRole.MEMBER
            )

    def test_cannot_assign_owner_role(self, group_conversation):
        """Test that owner role cannot be assigned directly."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ConversationAccessDeniedError):
            messaging.update_participant_role(
                owner.id, group.id, member1.id, ParticipantRole.OWNER
            )

    def test_change_role_in_dm_fails(self, dm_conversation):
        """Test that roles cannot be changed in DM."""
        dm, user1, user2, messaging = dm_conversation

        with pytest.raises(ConversationTypeError):
            messaging.update_participant_role(
                user1.id, dm.id, user2.id, ParticipantRole.ADMIN
            )

    def test_change_role_of_nonexistent_participant_fails(
        self, group_conversation, user_pool
    ):
        """Test that changing role of non-participant fails."""
        group, owner, member1, member2, messaging = group_conversation
        non_member = user_pool.get_user()

        with pytest.raises(ParticipantNotFoundError):
            messaging.update_participant_role(
                owner.id, group.id, non_member.id, ParticipantRole.ADMIN
            )


class TestParticipantListing:
    """Tests for listing participants."""

    def test_get_participants_in_dm(self, dm_conversation):
        """Test getting participants in DM."""
        dm, user1, user2, messaging = dm_conversation

        participants = messaging.get_participants(user1.id, dm.id)

        assert len(participants) == 2
        user_ids = [p.user_id for p in participants]
        assert user1.id in user_ids
        assert user2.id in user_ids

    def test_get_participants_in_group(self, group_conversation):
        """Test getting participants in group."""
        group, owner, member1, member2, messaging = group_conversation

        participants = messaging.get_participants(owner.id, group.id)

        assert len(participants) == 3
        user_ids = [p.user_id for p in participants]
        assert owner.id in user_ids
        assert member1.id in user_ids
        assert member2.id in user_ids

    def test_get_participants_non_member_fails(self, group_conversation, user_pool):
        """Test that non-members cannot get participant list."""
        group, owner, member1, member2, messaging = group_conversation
        non_member = user_pool.get_user()

        with pytest.raises(ConversationAccessDeniedError):
            messaging.get_participants(non_member.id, group.id)

    def test_participants_ordered_by_join_time(self, group_conversation):
        """Test that participants are ordered by join time."""
        group, owner, member1, member2, messaging = group_conversation

        participants = messaging.get_participants(owner.id, group.id)

        # Should be ordered by joined_at
        for i in range(len(participants) - 1):
            assert participants[i].joined_at <= participants[i + 1].joined_at


class TestParticipantPermissions:
    """Tests for participant-level permissions."""

    def test_participant_has_role(self, group_conversation):
        """Test that participant has correct role."""
        group, owner, member1, member2, messaging = group_conversation

        participant = messaging._get_participant(group.id, owner.id)
        assert participant.role == ParticipantRole.OWNER

        participant = messaging._get_participant(group.id, member1.id)
        assert participant.role == ParticipantRole.MEMBER

    def test_participant_joined_timestamp(self, group_conversation):
        """Test that participant has join timestamp."""
        group, owner, member1, member2, messaging = group_conversation

        participant = messaging._get_participant(group.id, member1.id)
        assert participant.joined_at > 0


class TestParticipantCaching:
    """Tests for participant caching."""

    def test_is_participant_cached(self, dm_conversation):
        """Test that participant checks are cached."""
        dm, user1, user2, messaging = dm_conversation

        # First check populates cache
        result1 = messaging._is_participant(dm.id, user1.id)

        # Second check uses cache
        result2 = messaging._is_participant(dm.id, user1.id)

        assert result1 is True
        assert result2 is True

    def test_participant_cache_invalidation_on_add(self, group_conversation, user_pool):
        """Test that cache is invalidated when participant added."""
        group, owner, member1, member2, messaging = group_conversation
        new_user = user_pool.get_user()

        # Check before adding
        result_before = messaging._is_participant(group.id, new_user.id)
        assert result_before is False

        # Add participant
        messaging.add_participant(owner.id, group.id, new_user.id)

        # Check after adding
        result_after = messaging._is_participant(group.id, new_user.id)
        assert result_after is True

    def test_participant_cache_invalidation_on_remove(self, group_conversation):
        """Test that cache is invalidated when participant removed."""
        group, owner, member1, member2, messaging = group_conversation

        # Check before removing
        result_before = messaging._is_participant(group.id, member1.id)
        assert result_before is True

        # Remove participant
        messaging.remove_participant(owner.id, group.id, member1.id)

        # Check after removing
        result_after = messaging._is_participant(group.id, member1.id)
        assert result_after is False


class TestParticipantEdgeCases:
    """Tests for participant edge cases."""

    def test_single_participant_group(self, user_pool, modules):
        """Test group with single participant (owner only)."""
        owner = user_pool.get_user()

        group = modules.messaging.create_group(owner_id=owner.id, name="Solo Group")

        participants = modules.messaging.get_participants(owner.id, group.id)
        assert len(participants) == 1
        assert participants[0].user_id == owner.id

    def test_group_with_max_participants(self, user_pool, modules):
        """Test group at maximum capacity."""
        owner = user_pool.get_user()

        # Create group with small limit
        group = modules.messaging.create_group(
            owner_id=owner.id, name="Full Group", max_participants=3
        )

        # Fill to capacity
        user1 = user_pool.get_user()
        user2 = user_pool.get_user()
        modules.messaging.add_participant(owner.id, group.id, user1.id)
        modules.messaging.add_participant(owner.id, group.id, user2.id)

        # Verify at capacity
        conv = modules.messaging.get_conversation(group.id, owner.id)
        assert conv.participant_count == conv.max_participants

    def test_reduce_max_participants_below_current(self, group_conversation):
        """Test that max participants cannot be reduced below current count."""
        group, owner, member1, member2, messaging = group_conversation

        with pytest.raises(ParticipantLimitError):
            messaging.update_conversation(
                owner.id,
                group.id,
                max_participants=2,  # Current is 3
            )
