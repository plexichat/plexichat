"""
Tests for friend request functionality.
"""

import pytest
from src.core.relationships import (
    FriendRequestStatus,
    RelationshipStatus,
    SelfRelationshipError,
    FriendRequestNotFoundError,
    FriendRequestExistsError,
    AlreadyFriendsError,
    PermissionDeniedError,
    UserBlockedError,
)


class TestSendFriendRequest:
    """Tests for sending friend requests."""

    def test_send_friend_request_success(self, fresh_users):
        """Test sending a friend request successfully."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        assert request is not None
        assert request.sender_id == user1.id
        assert request.recipient_id == user2.id
        assert request.status == FriendRequestStatus.PENDING
        assert request.created_at > 0

    def test_send_friend_request_with_message(self, fresh_users):
        """Test sending a friend request with a message."""
        user1, user2, relationships = fresh_users

        message = "Hey, want to be friends?"
        request = relationships.send_friend_request(user1.id, user2.id, message=message)

        assert request.message == message

    def test_send_friend_request_to_self_fails(self, users):
        """Test that sending a friend request to yourself fails."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(SelfRelationshipError):
            relationships.send_friend_request(user1.id, user1.id)

    def test_send_duplicate_request_fails(self, fresh_users):
        """Test that sending a duplicate request fails."""
        user1, user2, relationships = fresh_users

        relationships.send_friend_request(user1.id, user2.id)

        with pytest.raises(FriendRequestExistsError):
            relationships.send_friend_request(user1.id, user2.id)

    def test_send_request_to_blocked_user_fails(self, fresh_users):
        """Test that sending a request to a blocked user fails."""
        user1, user2, relationships = fresh_users

        relationships.block_user(user1.id, user2.id)

        with pytest.raises(UserBlockedError):
            relationships.send_friend_request(user1.id, user2.id)

    def test_send_request_when_blocked_by_user_fails(self, fresh_users):
        """Test that sending a request when blocked by user fails."""
        user1, user2, relationships = fresh_users

        relationships.block_user(user2.id, user1.id)

        with pytest.raises(UserBlockedError):
            relationships.send_friend_request(user1.id, user2.id)

    def test_send_request_to_existing_friend_fails(self, friends_pair):
        """Test that sending a request to an existing friend fails."""
        user1, user2, relationships = friends_pair

        with pytest.raises(AlreadyFriendsError):
            relationships.send_friend_request(user1.id, user2.id)

    def test_send_request_auto_accepts_reverse_request(self, fresh_users):
        """Test that sending a request auto-accepts if reverse request exists."""
        user1, user2, relationships = fresh_users

        # User2 sends request to user1
        relationships.send_friend_request(user2.id, user1.id)

        # User1 sends request to user2 - should auto-accept
        request = relationships.send_friend_request(user1.id, user2.id)

        assert request.status == FriendRequestStatus.ACCEPTED

        # They should now be friends
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND


class TestAcceptFriendRequest:
    """Tests for accepting friend requests."""

    def test_accept_friend_request_success(self, fresh_users):
        """Test accepting a friend request successfully."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        accepted = relationships.accept_friend_request(user2.id, request.id)

        assert accepted.status == FriendRequestStatus.ACCEPTED

        # Verify friendship
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

        rel2 = relationships.get_relationship(user2.id, user1.id)
        assert rel2.status == RelationshipStatus.FRIEND

    def test_accept_request_wrong_user_fails(self, fresh_users):
        """Test that accepting a request as wrong user fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        # User1 (sender) tries to accept
        with pytest.raises(PermissionDeniedError):
            relationships.accept_friend_request(user1.id, request.id)

    def test_accept_nonexistent_request_fails(self, users):
        """Test that accepting a nonexistent request fails."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(FriendRequestNotFoundError):
            relationships.accept_friend_request(user1.id, 999999999)

    def test_accept_already_processed_request_fails(self, fresh_users):
        """Test that accepting an already processed request fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.accept_friend_request(user2.id, request.id)

        with pytest.raises(FriendRequestNotFoundError):
            relationships.accept_friend_request(user2.id, request.id)


class TestDeclineFriendRequest:
    """Tests for declining friend requests."""

    def test_decline_friend_request_success(self, fresh_users):
        """Test declining a friend request successfully."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        declined = relationships.decline_friend_request(user2.id, request.id)

        assert declined.status == FriendRequestStatus.DECLINED

        # Verify no friendship
        rel = relationships.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE

    def test_decline_request_wrong_user_fails(self, fresh_users):
        """Test that declining a request as wrong user fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        with pytest.raises(PermissionDeniedError):
            relationships.decline_friend_request(user1.id, request.id)

    def test_decline_nonexistent_request_fails(self, users):
        """Test that declining a nonexistent request fails."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(FriendRequestNotFoundError):
            relationships.decline_friend_request(user1.id, 999999999)


class TestCancelFriendRequest:
    """Tests for cancelling friend requests."""

    def test_cancel_friend_request_success(self, fresh_users):
        """Test cancelling a friend request successfully."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        cancelled = relationships.cancel_friend_request(user1.id, request.id)

        assert cancelled.status == FriendRequestStatus.CANCELLED

    def test_cancel_request_wrong_user_fails(self, fresh_users):
        """Test that cancelling a request as wrong user fails."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)

        # User2 (recipient) tries to cancel
        with pytest.raises(PermissionDeniedError):
            relationships.cancel_friend_request(user2.id, request.id)

    def test_cancel_nonexistent_request_fails(self, users):
        """Test that cancelling a nonexistent request fails."""
        user1, user2, user3, user4, relationships = users

        with pytest.raises(FriendRequestNotFoundError):
            relationships.cancel_friend_request(user1.id, 999999999)


class TestGetPendingRequests:
    """Tests for getting pending friend requests."""

    def test_get_pending_requests_incoming(self, fresh_users):
        """Test getting incoming pending requests."""
        user1, user2, relationships = fresh_users

        relationships.send_friend_request(user1.id, user2.id)

        incoming = relationships.get_pending_requests_incoming(user2.id)

        assert len(incoming) == 1
        assert incoming[0].sender_id == user1.id

    def test_get_pending_requests_outgoing(self, fresh_users):
        """Test getting outgoing pending requests."""
        user1, user2, relationships = fresh_users

        relationships.send_friend_request(user1.id, user2.id)

        outgoing = relationships.get_pending_requests_outgoing(user1.id)

        assert len(outgoing) == 1
        assert outgoing[0].recipient_id == user2.id

    def test_get_pending_requests_empty(self, users):
        """Test getting pending requests when none exist."""
        user1, user2, user3, user4, relationships = users

        incoming = relationships.get_pending_requests_incoming(user4.id)
        outgoing = relationships.get_pending_requests_outgoing(user4.id)

        assert len(incoming) == 0
        assert len(outgoing) == 0

    def test_get_pending_requests_excludes_processed(self, fresh_users):
        """Test that processed requests are excluded."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        relationships.decline_friend_request(user2.id, request.id)

        incoming = relationships.get_pending_requests_incoming(user2.id)
        outgoing = relationships.get_pending_requests_outgoing(user1.id)

        assert len(incoming) == 0
        assert len(outgoing) == 0


class TestGetFriendRequest:
    """Tests for getting a specific friend request."""

    def test_get_friend_request_success(self, fresh_users):
        """Test getting a friend request by ID."""
        user1, user2, relationships = fresh_users

        request = relationships.send_friend_request(user1.id, user2.id)
        fetched = relationships.get_friend_request(request.id)

        assert fetched is not None
        assert fetched.id == request.id
        assert fetched.sender_id == user1.id
        assert fetched.recipient_id == user2.id

    def test_get_friend_request_not_found(self, users):
        """Test getting a nonexistent friend request."""
        user1, user2, user3, user4, relationships = users

        fetched = relationships.get_friend_request(999999999)

        assert fetched is None
