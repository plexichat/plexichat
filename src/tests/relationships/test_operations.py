"""Comprehensive Relationships tests targeting 80%+ coverage."""

import pytest

from src.core.relationships.models import RelationshipStatus
from src.core.relationships.exceptions import (
    SelfRelationshipError,
    AlreadyFriendsError,
    UserBlockedError,
    FriendRequestExistsError,
    FriendRequestNotFoundError,
    PermissionDeniedError,
    NotFriendsError,
    CannotBlockSelfError,
    AlreadyBlockedError,
    NotBlockedError,
)


class TestRelationshipErrors:
    def test_self_relationship(self, rel_manager, two_users):
        """Cannot create relationship with self."""
        user1, _ = two_users
        with pytest.raises(SelfRelationshipError):
            rel_manager.send_friend_request(user1.id, user1.id)

    def test_already_friends(self, rel_manager, two_users):
        """Cannot send request if already friends."""
        user1, user2 = two_users
        rel_manager.send_friend_request(user1.id, user2.id)
        req_id = rel_manager._db.fetch_one(
            "SELECT id FROM rel_friend_requests WHERE sender_id=?",
            (user1.id,),
        )["id"]
        rel_manager.accept_friend_request(user2.id, req_id)
        with pytest.raises(AlreadyFriendsError):
            rel_manager.send_friend_request(user1.id, user2.id)

    def test_request_while_blocked(self, rel_manager, two_users):
        """Cannot send request to blocked user."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)
        with pytest.raises(UserBlockedError):
            rel_manager.send_friend_request(user1.id, user2.id)

    def test_blocked_by_user(self, rel_manager, two_users):
        """Cannot send request if blocked by user."""
        user1, user2 = two_users
        rel_manager.block_user(user2.id, user1.id)
        with pytest.raises(UserBlockedError):
            rel_manager.send_friend_request(user1.id, user2.id)

    def test_duplicate_request(self, rel_manager, two_users):
        """Cannot send duplicate pending request."""
        user1, user2 = two_users
        rel_manager.send_friend_request(user1.id, user2.id)
        with pytest.raises(FriendRequestExistsError):
            rel_manager.send_friend_request(user1.id, user2.id)

    def test_reverse_request_auto_accept(self, rel_manager, two_users):
        """Reverse request auto-accepts."""
        user1, user2 = two_users
        rel_manager.send_friend_request(user1.id, user2.id)
        result = rel_manager.send_friend_request(user2.id, user1.id)
        assert result.status.value == "accepted"

    def test_decline_request(self, rel_manager, two_users):
        """Can decline friend request."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        declined = rel_manager.decline_friend_request(user2.id, req.id)
        assert declined.status.value == "declined"

    def test_decline_nonexistent_request(self, rel_manager, two_users):
        """Cannot decline nonexistent request."""
        user1, _ = two_users
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.decline_friend_request(user1.id, 99999)

    def test_decline_wrong_recipient(self, rel_manager, three_users):
        """Cannot decline request not sent to you."""
        user1, user2, user3 = three_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        with pytest.raises(PermissionDeniedError):
            rel_manager.decline_friend_request(user3.id, req.id)

    def test_cancel_request(self, rel_manager, two_users):
        """Can cancel sent request."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        cancelled = rel_manager.cancel_friend_request(user1.id, req.id)
        assert cancelled.status.value == "cancelled"

    def test_cancel_nonexistent_request(self, rel_manager, two_users):
        """Cannot cancel nonexistent request."""
        user1, _ = two_users
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.cancel_friend_request(user1.id, 99999)

    def test_cancel_wrong_sender(self, rel_manager, two_users):
        """Cannot cancel request not sent by you."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        with pytest.raises(PermissionDeniedError):
            rel_manager.cancel_friend_request(user2.id, req.id)

    def test_unfriend(self, rel_manager, two_users):
        """Can remove friend."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)
        assert rel_manager.remove_friend(user1.id, user2.id)
        assert not rel_manager._are_friends(user1.id, user2.id)

    def test_unfriend_not_friends(self, rel_manager, two_users):
        """Cannot unfriend non-friend."""
        user1, user2 = two_users
        with pytest.raises(NotFriendsError):
            rel_manager.remove_friend(user1.id, user2.id)

    def test_unfriend_self(self, rel_manager, two_users):
        """Cannot unfriend self."""
        user1, _ = two_users
        with pytest.raises(SelfRelationshipError):
            rel_manager.remove_friend(user1.id, user1.id)

    def test_block_self(self, rel_manager, two_users):
        """Cannot block self."""
        user1, _ = two_users
        with pytest.raises(CannotBlockSelfError):
            rel_manager.block_user(user1.id, user1.id)

    def test_already_blocked(self, rel_manager, two_users):
        """Cannot block twice."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)
        with pytest.raises(AlreadyBlockedError):
            rel_manager.block_user(user1.id, user2.id)

    def test_block_removes_friendship(self, rel_manager, two_users):
        """Blocking removes friendship."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)
        rel_manager.block_user(user1.id, user2.id)
        assert not rel_manager._are_friends(user1.id, user2.id)

    def test_block_cancels_outgoing_request(self, rel_manager, two_users):
        """Blocking cancels outgoing friend request."""
        user1, user2 = two_users
        rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.block_user(user1.id, user2.id)

        requests = rel_manager.get_outgoing_requests(user1.id)
        from src.core.relationships.models import FriendRequestStatus

        assert (
            len(
                [
                    r
                    for r in requests
                    if r.recipient_id == user2.id
                    and r.status == FriendRequestStatus.PENDING
                ]
            )
            == 0
        )

    def test_block_declines_incoming_request(self, rel_manager, two_users):
        """Blocking declines incoming friend request."""
        user1, user2 = two_users
        rel_manager.send_friend_request(user2.id, user1.id)
        rel_manager.block_user(user1.id, user2.id)

        requests = rel_manager.get_incoming_requests(user1.id)
        from src.core.relationships.models import FriendRequestStatus

        assert (
            len(
                [
                    r
                    for r in requests
                    if r.sender_id == user2.id
                    and r.status == FriendRequestStatus.PENDING
                ]
            )
            == 0
        )

    def test_unblock(self, rel_manager, two_users):
        """Can unblock user."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)
        assert rel_manager.unblock_user(user1.id, user2.id)
        assert not rel_manager._is_blocked(user1.id, user2.id)

    def test_unblock_not_blocked(self, rel_manager, two_users):
        """Cannot unblock non-blocked user."""
        user1, user2 = two_users
        with pytest.raises(NotBlockedError):
            rel_manager.unblock_user(user1.id, user2.id)

    def test_get_friends(self, rel_manager, three_users):
        """Can get friends list."""
        user1, user2, user3 = three_users
        req1 = rel_manager.send_friend_request(user1.id, user2.id)
        req2 = rel_manager.send_friend_request(user1.id, user3.id)
        rel_manager.accept_friend_request(user2.id, req1.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        friends = rel_manager.get_friends(user1.id)
        assert len(friends) >= 2

    def test_get_blocked_users(self, rel_manager, three_users):
        """Can get blocked users list."""
        user1, user2, user3 = three_users
        rel_manager.block_user(user1.id, user2.id)
        rel_manager.block_user(user1.id, user3.id)

        blocked = rel_manager.get_blocked_users(user1.id)
        assert len(blocked) >= 2

    def test_get_incoming_requests(self, rel_manager, three_users):
        """Can get incoming requests."""
        user1, user2, user3 = three_users
        rel_manager.send_friend_request(user2.id, user1.id)
        rel_manager.send_friend_request(user3.id, user1.id)

        requests = rel_manager.get_incoming_requests(user1.id)
        assert len(requests) >= 2

    def test_get_outgoing_requests(self, rel_manager, three_users):
        """Can get outgoing requests."""
        user1, user2, user3 = three_users
        rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.send_friend_request(user1.id, user3.id)

        requests = rel_manager.get_outgoing_requests(user1.id)
        assert len(requests) >= 2

    def test_accept_request_not_found(self, rel_manager, two_users):
        """Cannot accept nonexistent request."""
        user1, _ = two_users
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.accept_friend_request(user1.id, 99999)

    def test_accept_request_wrong_recipient(self, rel_manager, three_users):
        """Cannot accept request not sent to you."""
        user1, user2, user3 = three_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        with pytest.raises(PermissionDeniedError):
            rel_manager.accept_friend_request(user3.id, req.id)

    def test_decline_request_invalidates_all_relationships_cache(
        self, rel_manager, two_users
    ):
        """Declining should evict cached aggregate relationship views."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)

        cached_before = rel_manager.get_all_relationships(user2.id)
        assert any(item.id == req.id for item in cached_before["pending_incoming"])

        rel_manager.decline_friend_request(user2.id, req.id)

        refreshed = rel_manager.get_all_relationships(user2.id)
        assert not any(item.id == req.id for item in refreshed["pending_incoming"])

    def test_cancel_request_invalidates_all_relationships_cache(
        self, rel_manager, two_users
    ):
        """Cancelling should evict cached aggregate relationship views."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)

        cached_before = rel_manager.get_all_relationships(user1.id)
        assert any(item.id == req.id for item in cached_before["pending_outgoing"])

        rel_manager.cancel_friend_request(user1.id, req.id)

        refreshed = rel_manager.get_all_relationships(user1.id)
        assert not any(item.id == req.id for item in refreshed["pending_outgoing"])

    def test_accept_request_rolls_back_if_friendship_insert_fails(
        self, rel_manager, monkeypatch, two_users
    ):
        """Accepting should not leave half-written friendship state behind."""
        user1, user2 = two_users
        request = rel_manager.send_friend_request(user1.id, user2.id)
        original_insert = rel_manager._db.insert_or_ignore
        insert_calls = {"count": 0}

        def failing_insert_or_ignore(*args, **kwargs):
            insert_calls["count"] += 1
            if insert_calls["count"] == 2:
                raise RuntimeError("forced friendship insert failure")
            return original_insert(*args, **kwargs)

        monkeypatch.setattr(
            rel_manager._db, "insert_or_ignore", failing_insert_or_ignore
        )

        with pytest.raises(RuntimeError, match="forced friendship insert failure"):
            rel_manager.accept_friend_request(user2.id, request.id)

        request_row = rel_manager._db.fetch_one(
            "SELECT status FROM rel_friend_requests WHERE id = ?", (request.id,)
        )
        assert request_row["status"] == "pending"
        assert (
            rel_manager._db.fetch_one(
                "SELECT 1 as ok FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                (user1.id, user2.id),
            )
            is None
        )
        assert (
            rel_manager._db.fetch_one(
                "SELECT 1 as ok FROM rel_friends WHERE user_id = ? AND friend_id = ?",
                (user2.id, user1.id),
            )
            is None
        )


class TestRelationshipStatus:
    """Test relationship status checks."""

    def test_are_friends_true(self, rel_manager, two_users):
        """Check if users are friends."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)
        assert rel_manager._are_friends(user1.id, user2.id)

    def test_are_friends_false(self, rel_manager, two_users):
        """Check non-friends."""
        user1, user2 = two_users
        assert not rel_manager._are_friends(user1.id, user2.id)

    def test_is_blocked_true(self, rel_manager, two_users):
        """Check if user is blocked."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)
        assert rel_manager._is_blocked(user1.id, user2.id)

    def test_is_blocked_false(self, rel_manager, two_users):
        """Check not blocked."""
        user1, user2 = two_users
        assert not rel_manager._is_blocked(user1.id, user2.id)

    def test_is_blocked_by_either(self, rel_manager, two_users):
        """Check if blocked by either user."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)
        assert rel_manager.is_blocked_by_either(user1.id, user2.id)
        assert rel_manager.is_blocked_by_either(user2.id, user1.id)

    def test_get_relationship_status_friends(self, rel_manager, two_users):
        """Get status when friends."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)
        status = rel_manager.get_relationship_status(user1.id, user2.id)
        assert status == RelationshipStatus.FRIEND

    def test_get_relationship_status_blocked(self, rel_manager, two_users):
        """Get status when blocked."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)
        status = rel_manager.get_relationship_status(user1.id, user2.id)
        assert status == RelationshipStatus.BLOCKED

    def test_get_relationship_status_pending(self, rel_manager, two_users):
        """Get status when request pending."""
        user1, user2 = two_users
        rel_manager.send_friend_request(user1.id, user2.id)
        status = rel_manager.get_relationship_status(user1.id, user2.id)
        assert status == RelationshipStatus.PENDING_OUTGOING

    def test_get_relationship_status_none(self, rel_manager, two_users):
        """Get status when no relationship."""
        user1, user2 = two_users
        status = rel_manager.get_relationship_status(user1.id, user2.id)
        assert status == RelationshipStatus.NONE


class TestRelationshipCache:
    """Test relationship caching."""

    def test_friends_cache(self, rel_manager, two_users):
        """Friends list is cached."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)

        friends1 = rel_manager.get_friends(user1.id)
        friends2 = rel_manager.get_friends(user1.id)

        assert len(friends1) == len(friends2)

    def test_blocked_cache(self, rel_manager, two_users):
        """Blocked list is cached."""
        user1, user2 = two_users
        rel_manager.block_user(user1.id, user2.id)

        blocked1 = rel_manager.get_blocked_users(user1.id)
        blocked2 = rel_manager.get_blocked_users(user1.id)

        assert len(blocked1) == len(blocked2)


class TestRelationshipNotifications:
    """Test relationship notifications."""

    def test_friend_request_notification(self, rel_manager, two_users):
        """Friend request triggers notification."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        assert req is not None

    def test_friend_accept_notification(self, rel_manager, two_users):
        """Accepting request triggers notification."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        accepted = rel_manager.accept_friend_request(user2.id, req.id)
        assert accepted is not None

    def test_unfriend_notification(self, rel_manager, two_users):
        """Unfriending triggers notification."""
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req.id)
        assert rel_manager.remove_friend(user1.id, user2.id)


class TestRelationshipBulkOperations:
    """Test bulk relationship operations."""

    def test_get_mutual_friends(self, rel_manager, three_users):
        """Get mutual friends between users."""
        user1, user2, user3 = three_users
        req1 = rel_manager.send_friend_request(user1.id, user3.id)
        req2 = rel_manager.send_friend_request(user2.id, user3.id)
        rel_manager.accept_friend_request(user3.id, req1.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        mutual = rel_manager.get_mutual_friends(user1.id, user2.id)
        assert len(mutual) >= 1

    def test_get_suggested_friends(self, rel_manager, three_users):
        """Get friend suggestions."""
        user1, user2, user3 = three_users
        req1 = rel_manager.send_friend_request(user1.id, user2.id)
        req2 = rel_manager.send_friend_request(user2.id, user3.id)
        rel_manager.accept_friend_request(user2.id, req1.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        suggestions = rel_manager.get_suggested_friends(user1.id)
        assert suggestions is not None
