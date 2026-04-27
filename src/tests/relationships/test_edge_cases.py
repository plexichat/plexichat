"""
Tests for edge cases and error handling.
"""

import pytest
from src.core.relationships import (
    RelationshipStatus,
    FriendRequestStatus,
    SelfRelationshipError,
    FriendRequestNotFoundError,
    NotFriendsError,
    AlreadyBlockedError,
    NotBlockedError,
    PermissionDeniedError,
    CannotBlockSelfError,
)


class TestSelfRelationships:
    """Tests for self-relationship edge cases."""

    def test_cannot_friend_self(self, rel_manager, test_user):
        """Test that user cannot send friend request to self."""
        with pytest.raises(SelfRelationshipError):
            rel_manager.send_friend_request(test_user.id, test_user.id)

    def test_cannot_block_self(self, rel_manager, test_user):
        """Test that user cannot block self."""
        with pytest.raises(CannotBlockSelfError):
            rel_manager.block_user(test_user.id, test_user.id)

    def test_self_relationship_status_is_none(self, rel_manager, test_user):
        """Test that self relationship status is NONE."""
        rel = rel_manager.get_relationship(test_user.id, test_user.id)

        assert rel.status == RelationshipStatus.NONE

    def test_self_mutual_friends_empty(self, rel_manager, test_user):
        """Test that mutual friends with self is empty."""
        mutual = rel_manager.get_mutual_friends(test_user.id, test_user.id)

        assert len(mutual) == 0


class TestInvalidRequests:
    """Tests for invalid request handling."""

    def test_accept_nonexistent_request(self, rel_manager, test_user):
        """Test accepting a nonexistent request."""
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.accept_friend_request(test_user.id, 999999999999)

    def test_decline_nonexistent_request(self, rel_manager, test_user):
        """Test declining a nonexistent request."""
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.decline_friend_request(test_user.id, 999999999999)

    def test_cancel_nonexistent_request(self, rel_manager, test_user):
        """Test cancelling a nonexistent request."""
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.cancel_friend_request(test_user.id, 999999999999)

    def test_get_nonexistent_request(self, rel_manager):
        """Test getting a nonexistent request returns None."""
        request = rel_manager.get_friend_request(999999999999)

        assert request is None


class TestDoubleActions:
    """Tests for double action handling."""

    def test_double_accept_fails(self, rel_manager, two_users):
        """Test that accepting twice fails."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.accept_friend_request(user2.id, request.id)

    def test_double_decline_fails(self, rel_manager, two_users):
        """Test that declining twice fails."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.decline_friend_request(user2.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.decline_friend_request(user2.id, request.id)

    def test_double_cancel_fails(self, rel_manager, two_users):
        """Test that cancelling twice fails."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.cancel_friend_request(user1.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.cancel_friend_request(user1.id, request.id)

    def test_double_block_fails(self, rel_manager, two_users):
        """Test that blocking twice fails."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        with pytest.raises(AlreadyBlockedError):
            rel_manager.block_user(user1.id, user2.id)

    def test_double_unblock_fails(self, rel_manager, two_users):
        """Test that unblocking twice fails."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        rel_manager.unblock_user(user1.id, user2.id)

        with pytest.raises(NotBlockedError):
            rel_manager.unblock_user(user1.id, user2.id)

    def test_double_unfriend_fails(self, rel_manager, two_users):
        """Test that unfriending twice fails."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)
        rel_manager.remove_friend(user1.id, user2.id)

        with pytest.raises(NotFriendsError):
            rel_manager.remove_friend(user1.id, user2.id)


class TestWrongUserActions:
    """Tests for actions by wrong user."""

    def test_accept_as_sender_fails(self, rel_manager, two_users):
        """Test that sender cannot accept their own request."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            rel_manager.accept_friend_request(user1.id, request.id)

    def test_decline_as_sender_fails(self, rel_manager, two_users):
        """Test that sender cannot decline their own request."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            rel_manager.decline_friend_request(user1.id, request.id)

    def test_cancel_as_recipient_fails(self, rel_manager, two_users):
        """Test that recipient cannot cancel request."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            rel_manager.cancel_friend_request(user2.id, request.id)


class TestBlockingEdgeCases:
    """Tests for blocking edge cases."""

    def test_block_pending_request_sender(self, rel_manager, two_users):
        """Test blocking someone who sent you a request."""
        user1, user2 = two_users

        # User1 sends request to user2
        request = rel_manager.send_friend_request(user1.id, user2.id)

        # User2 blocks user1
        rel_manager.block_user(user2.id, user1.id)

        # Request should be declined
        updated = rel_manager.get_friend_request(request.id)
        assert updated.status == FriendRequestStatus.DECLINED

    def test_block_pending_request_recipient(self, rel_manager, two_users):
        """Test blocking someone you sent a request to."""
        user1, user2 = two_users

        # User1 sends request to user2
        request = rel_manager.send_friend_request(user1.id, user2.id)

        # User1 blocks user2
        rel_manager.block_user(user1.id, user2.id)

        # Request should be declined
        updated = rel_manager.get_friend_request(request.id)
        assert updated.status == FriendRequestStatus.DECLINED

    def test_unblock_does_not_restore_friendship(self, rel_manager, two_users):
        """Test that unblocking does not restore friendship."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)
        rel_manager.block_user(user1.id, user2.id)
        rel_manager.unblock_user(user1.id, user2.id)

        # Should not be friends anymore
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE


class TestRelationshipStatusEdgeCases:
    """Tests for relationship status edge cases."""

    def test_relationship_after_decline(self, rel_manager, two_users):
        """Test relationship status after declining request."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.decline_friend_request(user2.id, request.id)

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_relationship_after_cancel(self, rel_manager, two_users):
        """Test relationship status after cancelling request."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.cancel_friend_request(user1.id, request.id)

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_blocked_status_from_blocker_perspective(self, rel_manager, two_users):
        """Test blocked status from blocker perspective."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.BLOCKED

    def test_blocked_status_from_blocked_perspective(self, rel_manager, two_users):
        """Test relationship status from blocked user perspective."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)

        # From user2's perspective, they see NONE (they don't know they're blocked)
        rel = rel_manager.get_relationship(user2.id, user1.id)
        assert rel.status == RelationshipStatus.NONE


class TestEmptyLists:
    """Tests for empty list handling."""

    def test_empty_friends_list(self, rel_manager, test_user):
        """Test getting empty friends list."""
        friends = rel_manager.get_friends(test_user.id)
        assert len(friends) == 0

    def test_empty_blocked_list(self, rel_manager, test_user):
        """Test getting empty blocked list."""
        blocked = rel_manager.get_blocked_users(test_user.id)
        assert len(blocked) == 0

    def test_empty_incoming_requests(self, rel_manager, test_user):
        """Test getting empty incoming requests."""
        incoming = rel_manager.get_pending_requests_incoming(test_user.id)
        assert len(incoming) == 0

    def test_empty_outgoing_requests(self, rel_manager, test_user):
        """Test getting empty outgoing requests."""
        outgoing = rel_manager.get_pending_requests_outgoing(test_user.id)
        assert len(outgoing) == 0

    def test_empty_mutual_friends(self, rel_manager, two_users):
        """Test getting empty mutual friends."""
        user1, user2 = two_users

        mutual = rel_manager.get_mutual_friends(user1.id, user2.id)
        assert len(mutual) == 0

    def test_empty_mutual_servers(self, rel_manager, two_users):
        """Test getting empty mutual servers."""
        user1, user2 = two_users

        mutual = rel_manager.get_mutual_servers(user1.id, user2.id)
        assert len(mutual) == 0


class TestTimestamps:
    """Tests for timestamp handling."""

    def test_friend_request_has_timestamps(self, rel_manager, two_users):
        """Test that friend request has valid timestamps."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        assert request.created_at > 0
        assert request.updated_at > 0
        assert request.updated_at >= request.created_at

    def test_block_has_timestamp(self, rel_manager, two_users):
        """Test that block has valid timestamp."""
        user1, user2 = two_users

        block = rel_manager.block_user(user1.id, user2.id)

        assert block.created_at > 0

    def test_friendship_has_timestamp(self, rel_manager, two_users):
        """Test that friendship has valid timestamp."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        friends = rel_manager.get_friends(user1.id)

        assert len(friends) == 1
        assert friends[0].created_at > 0

    def test_relationship_has_timestamp(self, rel_manager, two_users):
        """Test that relationship status has timestamp."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.created_at > 0
