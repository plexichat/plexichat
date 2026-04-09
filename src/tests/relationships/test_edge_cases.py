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

    def test_cannot_friend_self(self, users):
        """Test that user cannot send friend request to self."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(SelfRelationshipError):
            relationships.send_friend_request(user1.id, user1.id)

    def test_cannot_block_self(self, users):
        """Test that user cannot block self."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(CannotBlockSelfError):
            relationships.block_user(user1.id, user1.id)

    def test_self_relationship_status_is_none(self, users):
        """Test that self relationship status is NONE."""
        user1, user2, user3, user4, relationships = users

        rel = relationships.get_relationship(user1.id, user1.id)

        assert rel.status == RelationshipStatus.NONE

    def test_self_mutual_friends_empty(self, users):
        """Test that mutual friends with self is empty."""
        user1, user2, user3, user4, relationships = users

        mutual = relationships.get_mutual_friends(user1.id, user1.id)

        assert len(mutual) == 0


class TestInvalidRequests:
    """Tests for invalid request handling."""

    def test_accept_nonexistent_request(self, users):
        """Test accepting a nonexistent request."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(FriendRequestNotFoundError):
            relationships.accept_friend_request(user1.id, 999999999999)

    def test_decline_nonexistent_request(self, users):
        """Test declining a nonexistent request."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(FriendRequestNotFoundError):
            relationships.decline_friend_request(user1.id, 999999999999)

    def test_cancel_nonexistent_request(self, users):
        """Test cancelling a nonexistent request."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(FriendRequestNotFoundError):
            relationships.cancel_friend_request(user1.id, 999999999999)

    def test_get_nonexistent_request(self, users):
        """Test getting a nonexistent request returns None."""
        user1, user2, user3, user4, relationships = users

        request = relationships.get_friend_request(999999999999)

        assert request is None


class TestDoubleActions:
    """Tests for double action handling."""

    def test_double_accept_fails(self, fresh_users):
        """Test that accepting twice fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.accept_friend_request(user2.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            relationships.accept_friend_request(user2.id, request.id)

    def test_double_decline_fails(self, fresh_users):
        """Test that declining twice fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.decline_friend_request(user2.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            relationships.decline_friend_request(user2.id, request.id)

    def test_double_cancel_fails(self, fresh_users):
        """Test that cancelling twice fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.cancel_friend_request(user1.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            relationships.cancel_friend_request(user1.id, request.id)

    def test_double_block_fails(self, fresh_users):
        """Test that blocking twice fails."""
        user1, user2, relationships = fresh_users

        relationships.block_user(user1.id, user2.id)

        with pytest.raises(AlreadyBlockedError):
            relationships.block_user(user1.id, user2.id)

    def test_double_unblock_fails(self, fresh_users):
        """Test that unblocking twice fails."""
        user1, user2, relationships = fresh_users

        relationships.block_user(user1.id, user2.id)
        relationships.unblock_user(user1.id, user2.id)

        with pytest.raises(NotBlockedError):
            relationships.unblock_user(user1.id, user2.id)

    def test_double_unfriend_fails(self, friends_pair):
        """Test that unfriending twice fails."""
        user1, user2, relationships = friends_pair

        relationships.remove_friend(user1.id, user2.id)

        with pytest.raises(NotFriendsError):
            relationships.remove_friend(user1.id, user2.id)


class TestWrongUserActions:
    """Tests for actions by wrong user."""

    def test_accept_as_sender_fails(self, fresh_users):
        """Test that sender cannot accept their own request."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            relationships.accept_friend_request(user1.id, request.id)

    def test_decline_as_sender_fails(self, fresh_users):
        """Test that sender cannot decline their own request."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            relationships.decline_friend_request(user1.id, request.id)

    def test_cancel_as_recipient_fails(self, fresh_users):
        """Test that recipient cannot cancel request."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            relationships.cancel_friend_request(user2.id, request.id)


class TestBlockingEdgeCases:
    """Tests for blocking edge cases."""

    def test_block_pending_request_sender(self, fresh_users):
        """Test blocking someone who sent you a request."""
        user1, user2, relationships = fresh_users

        # User1 sends request to user2
        request = relationships.send_friend_request(user1.id, user2.id)

        # User2 blocks user1
        relationships.block_user(user2.id, user1.id)

        # Request should be declined
        updated = relationships.get_friend_request(request.id)
        assert updated.status == FriendRequestStatus.DECLINED

    def test_block_pending_request_recipient(self, fresh_users):
        """Test blocking someone you sent a request to."""
        user1, user2, relationships = fresh_users

        # User1 sends request to user2
        request = relationships.send_friend_request(user1.id, user2.id)

        # User1 blocks user2
        relationships.block_user(user1.id, user2.id)

        # Request should be declined
        updated = relationships.get_friend_request(request.id)
        assert updated.status == FriendRequestStatus.DECLINED

    def test_unblock_does_not_restore_friendship(self, friends_pair):
        """Test that unblocking does not restore friendship."""
        user1, user2, relationships = friends_pair

        relationships.block_user(user1.id, user2.id)
        relationships.unblock_user(user1.id, user2.id)

        # Should not be friends anymore
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE


class TestRelationshipStatusEdgeCases:
    """Tests for relationship status edge cases."""

    def test_relationship_after_decline(self, fresh_users):
        """Test relationship status after declining request."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.decline_friend_request(user2.id, request.id)

        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_relationship_after_cancel(self, fresh_users):
        """Test relationship status after cancelling request."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.cancel_friend_request(user1.id, request.id)

        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_blocked_status_from_blocker_perspective(self, fresh_users):
        """Test blocked status from blocker perspective."""
        user1, user2, relationships = fresh_users

        relationships.block_user(user1.id, user2.id)

        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.BLOCKED

    def test_blocked_status_from_blocked_perspective(self, fresh_users):
        """Test relationship status from blocked user perspective."""
        user1, user2, relationships = fresh_users

        relationships.block_user(user1.id, user2.id)

        # From user2's perspective, they see NONE (they don't know they're blocked)
        rel = relationships.get_relationship(user2.id, user1.id)
        assert rel.status == RelationshipStatus.NONE


class TestEmptyLists:
    """Tests for empty list handling."""

    def test_empty_friends_list(self, users):
        """Test getting empty friends list."""
        user1, user2, user3, user4, relationships = users

        friends = relationships.get_friends(user4.id)
        assert len(friends) == 0

    def test_empty_blocked_list(self, users):
        """Test getting empty blocked list."""
        user1, user2, user3, user4, relationships = users

        blocked = relationships.get_blocked_users(user4.id)
        assert len(blocked) == 0

    def test_empty_incoming_requests(self, users):
        """Test getting empty incoming requests."""
        user1, user2, user3, user4, relationships = users

        incoming = relationships.get_pending_requests_incoming(user4.id)
        assert len(incoming) == 0

    def test_empty_outgoing_requests(self, users):
        """Test getting empty outgoing requests."""
        user1, user2, user3, user4, relationships = users

        outgoing = relationships.get_pending_requests_outgoing(user4.id)
        assert len(outgoing) == 0

    def test_empty_mutual_friends(self, fresh_users):
        """Test getting empty mutual friends."""
        user1, user2, relationships = fresh_users

        mutual = relationships.get_mutual_friends(user1.id, user2.id)
        assert len(mutual) == 0

    def test_empty_mutual_servers(self, fresh_users):
        """Test getting empty mutual servers."""
        user1, user2, relationships = fresh_users

        mutual = relationships.get_mutual_servers(user1.id, user2.id)
        assert len(mutual) == 0


class TestTimestamps:
    """Tests for timestamp handling."""

    def test_friend_request_has_timestamps(self, fresh_users):
        """Test that friend request has valid timestamps."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        assert request.created_at > 0
        assert request.updated_at > 0
        assert request.updated_at >= request.created_at

    def test_block_has_timestamp(self, fresh_users):
        """Test that block has valid timestamp."""
        user1, user2, relationships = fresh_users

        block = relationships.block_user(user1.id, user2.id)

        assert block.created_at > 0

    def test_friendship_has_timestamp(self, friends_pair):
        """Test that friendship has valid timestamp."""
        user1, user2, relationships = friends_pair

        friends = relationships.get_friends(user1.id)

        assert len(friends) == 1
        assert friends[0].created_at > 0

    def test_relationship_has_timestamp(self, friends_pair):
        """Test that relationship status has timestamp."""
        user1, user2, relationships = friends_pair

        rel = relationships.get_relationship(user1.id, user2.id)

        assert rel.created_at > 0
