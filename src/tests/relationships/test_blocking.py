"""
Tests for blocking functionality.
"""

import pytest
from src.core.relationships import (
    RelationshipStatus,
    FriendRequestStatus,
    CannotBlockSelfError,
    AlreadyBlockedError,
    NotBlockedError,
    UserBlockedError,
)


class TestBlockUser:
    """Tests for blocking users."""

    def test_block_user_success(self, rel_manager, two_users):
        """Test blocking a user successfully."""
        user1, user2 = two_users

        block = rel_manager.block_user(user1.id, user2.id)

        assert block is not None
        assert block.blocker_id == user1.id
        assert block.blocked_id == user2.id
        assert block.created_at > 0

    def test_block_user_with_reason(self, rel_manager, two_users):
        """Test blocking a user with a reason."""
        user1, user2 = two_users

        reason = "Spam messages"
        block = rel_manager.block_user(user1.id, user2.id, reason=reason)

        assert block.reason == reason

    def test_block_self_fails(self, rel_manager, test_user):
        """Test that blocking yourself fails."""
        with pytest.raises(CannotBlockSelfError):
            rel_manager.block_user(test_user.id, test_user.id)

    def test_block_already_blocked_fails(self, rel_manager, two_users):
        """Test that blocking an already blocked user fails."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        with pytest.raises(AlreadyBlockedError):
            rel_manager.block_user(user1.id, user2.id)

    def test_block_removes_friendship(self, rel_manager, two_users):
        """Test that blocking removes existing friendship."""
        user1, user2 = two_users

        # Create friendship
        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        # Verify they are friends
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

        # Block
        rel_manager.block_user(user1.id, user2.id)

        # Verify friendship is removed
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.BLOCKED

        # Verify from other side too
        friends = rel_manager.get_friend_ids(user2.id)
        assert user1.id not in friends

    def test_block_declines_pending_requests(self, rel_manager, two_users):
        """Test that blocking declines pending friend requests."""
        user1, user2 = two_users

        # Send request
        request = rel_manager.send_friend_request(user1.id, user2.id)
        assert request.status == FriendRequestStatus.PENDING

        # Block
        rel_manager.block_user(user2.id, user1.id)

        # Verify request is declined
        updated = rel_manager.get_friend_request(request.id)
        assert updated.status == FriendRequestStatus.DECLINED

    def test_block_declines_reverse_pending_requests(self, rel_manager, two_users):
        """Test that blocking declines reverse pending friend requests."""
        user1, user2 = two_users

        # User2 sends request to user1
        request = rel_manager.send_friend_request(user2.id, user1.id)

        # User1 blocks user2
        rel_manager.block_user(user1.id, user2.id)

        # Verify request is declined
        updated = rel_manager.get_friend_request(request.id)
        assert updated.status == FriendRequestStatus.DECLINED


class TestUnblockUser:
    """Tests for unblocking users."""

    def test_unblock_user_success(self, rel_manager, two_users):
        """Test unblocking a user successfully."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        result = rel_manager.unblock_user(user1.id, user2.id)

        assert result is True

        # Verify unblocked
        is_blocked = rel_manager.is_blocked(user1.id, user2.id)
        assert is_blocked is False

    def test_unblock_not_blocked_fails(self, rel_manager, two_users):
        """Test that unblocking a non-blocked user fails."""
        user1, user2 = two_users

        with pytest.raises(NotBlockedError):
            rel_manager.unblock_user(user1.id, user2.id)

    def test_unblock_allows_new_requests(self, rel_manager, two_users):
        """Test that unblocking allows new friend requests."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        rel_manager.unblock_user(user1.id, user2.id)

        # Should be able to send request now
        request = rel_manager.send_friend_request(user1.id, user2.id)
        assert request is not None


class TestGetBlockedUsers:
    """Tests for getting blocked users."""

    def test_get_blocked_users_success(self, rel_manager, two_users):
        """Test getting blocked users list."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        blocked = rel_manager.get_blocked_users(user1.id)

        assert len(blocked) == 1
        assert blocked[0].blocked_id == user2.id

    def test_get_blocked_users_empty(self, rel_manager, three_users):
        """Test getting blocked users when none exist."""
        user1, user2, user3 = three_users

        blocked = rel_manager.get_blocked_users(user3.id)

        assert len(blocked) == 0

    def test_get_blocked_user_ids(self, rel_manager, two_users):
        """Test getting blocked user IDs."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        blocked_ids = rel_manager.get_blocked_user_ids(user1.id)

        assert user2.id in blocked_ids


class TestIsBlocked:
    """Tests for checking block status."""

    def test_is_blocked_true(self, rel_manager, two_users):
        """Test is_blocked returns True when blocked."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        assert rel_manager.is_blocked(user1.id, user2.id) is True

    def test_is_blocked_false(self, rel_manager, two_users):
        """Test is_blocked returns False when not blocked."""
        user1, user2 = two_users

        assert rel_manager.is_blocked(user1.id, user2.id) is False

    def test_is_blocked_not_symmetric(self, rel_manager, two_users):
        """Test that blocking is not symmetric."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        # User1 blocked user2
        assert rel_manager.is_blocked(user1.id, user2.id) is True
        # User2 did not block user1
        assert rel_manager.is_blocked(user2.id, user1.id) is False

    def test_is_blocked_by_either(self, rel_manager, two_users):
        """Test is_blocked_by_either checks both directions."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        # Either direction should return True
        assert rel_manager.is_blocked_by_either(user1.id, user2.id) is True
        assert rel_manager.is_blocked_by_either(user2.id, user1.id) is True

    def test_is_blocked_by_either_false(self, rel_manager, two_users):
        """Test is_blocked_by_either returns False when neither blocked."""
        user1, user2 = two_users

        assert rel_manager.is_blocked_by_either(user1.id, user2.id) is False


class TestBlockEffects:
    """Tests for side effects of blocking."""

    def test_blocked_user_cannot_send_request(self, rel_manager, two_users):
        """Test that a blocked user cannot send friend request."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        # User2 tries to send request to user1
        with pytest.raises(UserBlockedError):
            rel_manager.send_friend_request(user2.id, user1.id)

    def test_blocker_cannot_send_request_to_blocked(self, rel_manager, two_users):
        """Test that blocker cannot send request to blocked user."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        # User1 tries to send request to user2 (whom they blocked)
        with pytest.raises(UserBlockedError):
            rel_manager.send_friend_request(user1.id, user2.id)

    def test_mutual_block(self, rel_manager, two_users):
        """Test that both users can block each other."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        rel_manager.block_user(user2.id, user1.id)

        assert rel_manager.is_blocked(user1.id, user2.id) is True
        assert rel_manager.is_blocked(user2.id, user1.id) is True

    def test_unblock_one_direction_only(self, rel_manager, two_users):
        """Test that unblocking only affects one direction."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        rel_manager.block_user(user2.id, user1.id)

        rel_manager.unblock_user(user1.id, user2.id)

        assert rel_manager.is_blocked(user1.id, user2.id) is False
        assert rel_manager.is_blocked(user2.id, user1.id) is True
