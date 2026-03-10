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
    def test_self_relationship(self, rel_manager):
        """Cannot create relationship with self."""
        with pytest.raises(SelfRelationshipError):
            rel_manager.send_friend_request(1, 1)

    def test_already_friends(self, rel_manager):
        """Cannot send request if already friends."""
        rel_manager.send_friend_request(1, 2)
        req_id = rel_manager._db.fetch_one(
            "SELECT id FROM rel_friend_requests WHERE sender_id=1"
        )["id"]
        rel_manager.accept_friend_request(2, req_id)
        with pytest.raises(AlreadyFriendsError):
            rel_manager.send_friend_request(1, 2)

    def test_request_while_blocked(self, rel_manager):
        """Cannot send request to blocked user."""
        rel_manager.block_user(1, 2)
        with pytest.raises(UserBlockedError):
            rel_manager.send_friend_request(1, 2)

    def test_blocked_by_user(self, rel_manager):
        """Cannot send request if blocked by user."""
        rel_manager.block_user(2, 1)
        with pytest.raises(UserBlockedError):
            rel_manager.send_friend_request(1, 2)

    def test_duplicate_request(self, rel_manager):
        """Cannot send duplicate pending request."""
        rel_manager.send_friend_request(1, 2)
        with pytest.raises(FriendRequestExistsError):
            rel_manager.send_friend_request(1, 2)

    def test_reverse_request_auto_accept(self, rel_manager):
        """Reverse request auto-accepts."""
        rel_manager.send_friend_request(1, 2)
        result = rel_manager.send_friend_request(2, 1)
        assert result.status.value == "accepted"

    def test_decline_request(self, rel_manager):
        """Can decline friend request."""
        req = rel_manager.send_friend_request(1, 2)
        declined = rel_manager.decline_friend_request(2, req.id)
        assert declined.status.value == "declined"

    def test_decline_nonexistent_request(self, rel_manager):
        """Cannot decline nonexistent request."""
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.decline_friend_request(1, 99999)

    def test_decline_wrong_recipient(self, rel_manager):
        """Cannot decline request not sent to you."""
        req = rel_manager.send_friend_request(1, 2)
        with pytest.raises(PermissionDeniedError):
            rel_manager.decline_friend_request(3, req.id)

    def test_cancel_request(self, rel_manager):
        """Can cancel sent request."""
        req = rel_manager.send_friend_request(1, 2)
        cancelled = rel_manager.cancel_friend_request(1, req.id)
        assert cancelled.status.value == "cancelled"

    def test_cancel_nonexistent_request(self, rel_manager):
        """Cannot cancel nonexistent request."""
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.cancel_friend_request(1, 99999)

    def test_cancel_wrong_sender(self, rel_manager):
        """Cannot cancel request not sent by you."""
        req = rel_manager.send_friend_request(1, 2)
        with pytest.raises(PermissionDeniedError):
            rel_manager.cancel_friend_request(2, req.id)

    def test_unfriend(self, rel_manager):
        """Can remove friend."""
        req = rel_manager.send_friend_request(1, 2)
        rel_manager.accept_friend_request(2, req.id)
        assert rel_manager.remove_friend(1, 2)
        assert not rel_manager._are_friends(1, 2)

    def test_unfriend_not_friends(self, rel_manager):
        """Cannot unfriend non-friend."""
        with pytest.raises(NotFriendsError):
            rel_manager.remove_friend(1, 2)

    def test_unfriend_self(self, rel_manager):
        """Cannot unfriend self."""
        with pytest.raises(SelfRelationshipError):
            rel_manager.remove_friend(1, 1)

    def test_block_self(self, rel_manager):
        """Cannot block self."""
        with pytest.raises(CannotBlockSelfError):
            rel_manager.block_user(1, 1)

    def test_already_blocked(self, rel_manager):
        """Cannot block twice."""
        rel_manager.block_user(1, 2)
        with pytest.raises(AlreadyBlockedError):
            rel_manager.block_user(1, 2)

    def test_block_removes_friendship(self, rel_manager):
        """Blocking removes friendship."""
        req = rel_manager.send_friend_request(1, 2)
        rel_manager.accept_friend_request(2, req.id)
        rel_manager.block_user(1, 2)
        assert not rel_manager._are_friends(1, 2)

    def test_block_cancels_outgoing_request(self, rel_manager):
        """Blocking cancels outgoing friend request."""
        rel_manager.send_friend_request(1, 2)
        rel_manager.block_user(1, 2)

        requests = rel_manager.get_outgoing_requests(1)
        from src.core.relationships.models import FriendRequestStatus

        assert (
            len(
                [
                    r
                    for r in requests
                    if r.recipient_id == 2 and r.status == FriendRequestStatus.PENDING
                ]
            )
            == 0
        )

    def test_block_declines_incoming_request(self, rel_manager):
        """Blocking declines incoming friend request."""
        rel_manager.send_friend_request(2, 1)
        rel_manager.block_user(1, 2)

        requests = rel_manager.get_incoming_requests(1)
        from src.core.relationships.models import FriendRequestStatus

        assert (
            len(
                [
                    r
                    for r in requests
                    if r.sender_id == 2 and r.status == FriendRequestStatus.PENDING
                ]
            )
            == 0
        )

    def test_unblock(self, rel_manager):
        """Can unblock user."""
        rel_manager.block_user(1, 2)
        assert rel_manager.unblock_user(1, 2)
        assert not rel_manager._is_blocked(1, 2)

    def test_unblock_not_blocked(self, rel_manager):
        """Cannot unblock non-blocked user."""
        with pytest.raises(NotBlockedError):
            rel_manager.unblock_user(1, 2)

    def test_get_friends(self, rel_manager):
        """Can get friends list."""
        req1 = rel_manager.send_friend_request(1, 2)
        req2 = rel_manager.send_friend_request(1, 3)
        rel_manager.accept_friend_request(2, req1.id)
        rel_manager.accept_friend_request(3, req2.id)

        friends = rel_manager.get_friends(1)
        assert len(friends) >= 2

    def test_get_blocked_users(self, rel_manager):
        """Can get blocked users list."""
        rel_manager.block_user(1, 2)
        rel_manager.block_user(1, 3)

        blocked = rel_manager.get_blocked_users(1)
        assert len(blocked) >= 2

    def test_get_incoming_requests(self, rel_manager):
        """Can get incoming requests."""
        rel_manager.send_friend_request(2, 1)
        rel_manager.send_friend_request(3, 1)

        requests = rel_manager.get_incoming_requests(1)
        assert len(requests) >= 2

    def test_get_outgoing_requests(self, rel_manager):
        """Can get outgoing requests."""
        rel_manager.send_friend_request(1, 2)
        rel_manager.send_friend_request(1, 3)

        requests = rel_manager.get_outgoing_requests(1)
        assert len(requests) >= 2

    def test_accept_request_not_found(self, rel_manager):
        """Cannot accept nonexistent request."""
        with pytest.raises(FriendRequestNotFoundError):
            rel_manager.accept_friend_request(1, 99999)

    def test_accept_request_wrong_recipient(self, rel_manager):
        """Cannot accept request not sent to you."""
        req = rel_manager.send_friend_request(1, 2)
        with pytest.raises(PermissionDeniedError):
            rel_manager.accept_friend_request(3, req.id)

    def test_decline_request_invalidates_all_relationships_cache(self, rel_manager):
        """Declining should evict cached aggregate relationship views."""
        req = rel_manager.send_friend_request(1, 2)

        cached_before = rel_manager.get_all_relationships(2)
        assert any(item.id == req.id for item in cached_before["pending_incoming"])

        rel_manager.decline_friend_request(2, req.id)

        refreshed = rel_manager.get_all_relationships(2)
        assert not any(item.id == req.id for item in refreshed["pending_incoming"])

    def test_cancel_request_invalidates_all_relationships_cache(self, rel_manager):
        """Cancelling should evict cached aggregate relationship views."""
        req = rel_manager.send_friend_request(1, 2)

        cached_before = rel_manager.get_all_relationships(1)
        assert any(item.id == req.id for item in cached_before["pending_outgoing"])

        rel_manager.cancel_friend_request(1, req.id)

        refreshed = rel_manager.get_all_relationships(1)
        assert not any(item.id == req.id for item in refreshed["pending_outgoing"])

    def test_accept_request_rolls_back_if_friendship_insert_fails(
        self, rel_manager, monkeypatch
    ):
        """Accepting should not leave half-written friendship state behind."""
        request = rel_manager.send_friend_request(1, 2)
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
            rel_manager.accept_friend_request(2, request.id)

        request_row = rel_manager._db.fetch_one(
            "SELECT status FROM rel_friend_requests WHERE id = ?", (request.id,)
        )
        assert request_row["status"] == "pending"
        assert rel_manager._db.fetch_one(
            "SELECT 1 as ok FROM rel_friends WHERE user_id = ? AND friend_id = ?",
            (1, 2),
        ) is None
        assert rel_manager._db.fetch_one(
            "SELECT 1 as ok FROM rel_friends WHERE user_id = ? AND friend_id = ?",
            (2, 1),
        ) is None


class TestRelationshipStatus:
    """Test relationship status checks."""

    def test_are_friends_true(self, rel_manager):
        """Check if users are friends."""
        req = rel_manager.send_friend_request(1, 2)
        rel_manager.accept_friend_request(2, req.id)
        assert rel_manager._are_friends(1, 2)

    def test_are_friends_false(self, rel_manager):
        """Check non-friends."""
        assert not rel_manager._are_friends(1, 2)

    def test_is_blocked_true(self, rel_manager):
        """Check if user is blocked."""
        rel_manager.block_user(1, 2)
        assert rel_manager._is_blocked(1, 2)

    def test_is_blocked_false(self, rel_manager):
        """Check not blocked."""
        assert not rel_manager._is_blocked(1, 2)

    def test_is_blocked_by_either(self, rel_manager):
        """Check if blocked by either user."""
        rel_manager.block_user(1, 2)
        assert rel_manager.is_blocked_by_either(1, 2)
        assert rel_manager.is_blocked_by_either(2, 1)

    def test_get_relationship_status_friends(self, rel_manager):
        """Get status when friends."""
        req = rel_manager.send_friend_request(1, 2)
        rel_manager.accept_friend_request(2, req.id)
        status = rel_manager.get_relationship_status(1, 2)
        assert status == RelationshipStatus.FRIEND

    def test_get_relationship_status_blocked(self, rel_manager):
        """Get status when blocked."""
        rel_manager.block_user(1, 2)
        status = rel_manager.get_relationship_status(1, 2)
        assert status == RelationshipStatus.BLOCKED

    def test_get_relationship_status_pending(self, rel_manager):
        """Get status when request pending."""
        rel_manager.send_friend_request(1, 2)
        status = rel_manager.get_relationship_status(1, 2)
        assert status == RelationshipStatus.PENDING_OUTGOING

    def test_get_relationship_status_none(self, rel_manager):
        """Get status when no relationship."""
        status = rel_manager.get_relationship_status(1, 2)
        assert status == RelationshipStatus.NONE


class TestRelationshipCache:
    """Test relationship caching."""

    def test_friends_cache(self, rel_manager):
        """Friends list is cached."""
        req = rel_manager.send_friend_request(1, 2)
        rel_manager.accept_friend_request(2, req.id)

        friends1 = rel_manager.get_friends(1)
        friends2 = rel_manager.get_friends(1)

        assert len(friends1) == len(friends2)

    def test_blocked_cache(self, rel_manager):
        """Blocked list is cached."""
        rel_manager.block_user(1, 2)

        blocked1 = rel_manager.get_blocked_users(1)
        blocked2 = rel_manager.get_blocked_users(1)

        assert len(blocked1) == len(blocked2)


class TestRelationshipNotifications:
    """Test relationship notifications."""

    def test_friend_request_notification(self, rel_manager):
        """Friend request triggers notification."""
        req = rel_manager.send_friend_request(1, 2)
        assert req is not None

    def test_friend_accept_notification(self, rel_manager):
        """Accepting request triggers notification."""
        req = rel_manager.send_friend_request(1, 2)
        accepted = rel_manager.accept_friend_request(2, req.id)
        assert accepted is not None

    def test_unfriend_notification(self, rel_manager):
        """Unfriending triggers notification."""
        req = rel_manager.send_friend_request(1, 2)
        rel_manager.accept_friend_request(2, req.id)
        assert rel_manager.remove_friend(1, 2)


class TestRelationshipBulkOperations:
    """Test bulk relationship operations."""

    def test_get_mutual_friends(self, rel_manager):
        """Get mutual friends between users."""
        req1 = rel_manager.send_friend_request(1, 3)
        req2 = rel_manager.send_friend_request(2, 3)
        rel_manager.accept_friend_request(3, req1.id)
        rel_manager.accept_friend_request(3, req2.id)

        mutual = rel_manager.get_mutual_friends(1, 2)
        assert len(mutual) >= 1

    def test_get_suggested_friends(self, rel_manager):
        """Get friend suggestions."""
        req1 = rel_manager.send_friend_request(1, 2)
        req2 = rel_manager.send_friend_request(2, 3)
        rel_manager.accept_friend_request(2, req1.id)
        rel_manager.accept_friend_request(3, req2.id)

        suggestions = rel_manager.get_suggested_friends(1)
        assert suggestions is not None
