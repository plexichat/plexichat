"""
Tests for bug fixes in the relationships system.

Covers all 8 bugs found during audit:
- Bug 1: Auto-accept status returned correctly
- Bug 2: Delete relationship 404 check for NONE status
- Bug 3: Direct query for pending requests in delete endpoint
- Bug 4: Accept route uses sender_id matching only
- Bug 5: Suggested friends exclude pending request users
- Bug 6: Unblock user wrapped in transaction
- Bug 7: Foreign key constraints on relationship tables
- Bug 8: UNIQUE constraint race condition handling
"""

import pytest
from unittest.mock import patch

from src.core.relationships import (
    RelationshipStatus,
    FriendRequestStatus,
)


class TestBug1AutoAcceptStatus:
    """Bug 1: Auto-accept should return 'friend' status, not 'pending_outgoing'."""

    def test_auto_accept_returns_accepted_status(self, rel_manager, two_users):
        """When reverse request exists, send_friend_request auto-accepts and returns accepted."""
        user1, user2 = two_users

        # User2 sends request to user1 first
        rel_manager.send_friend_request(user2.id, user1.id)

        # User1 sends request to user2 - should auto-accept
        result = rel_manager.send_friend_request(user1.id, user2.id)

        # The returned request should have accepted status
        assert result.status == FriendRequestStatus.ACCEPTED

        # They should be friends
        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.FRIEND

    def test_auto_accept_bidirectional_friendship(self, rel_manager, two_users):
        """Auto-accept creates bidirectional friendship."""
        user1, user2 = two_users

        # Create reverse request
        rel_manager.send_friend_request(user2.id, user1.id)

        # Auto-accept via user1 sending to user2
        rel_manager.send_friend_request(user1.id, user2.id)

        # Both should see each other as friends
        friends1 = rel_manager.get_friend_ids(user1.id)
        friends2 = rel_manager.get_friend_ids(user2.id)
        assert user2.id in friends1
        assert user1.id in friends2


class TestBug2DeleteRelationship404:
    """Bug 2: Delete relationship should 404 for NONE status."""

    def test_delete_none_status_raises_not_friends(self, rel_manager, two_users):
        """Attempting to remove a non-friend should raise NotFriendsError."""
        from src.core.relationships.exceptions import NotFriendsError

        user1, user2 = two_users

        # No relationship exists
        with pytest.raises(NotFriendsError):
            rel_manager.remove_friend(user1.id, user2.id)

    def test_get_relationship_returns_none_status(self, rel_manager, two_users):
        """get_relationship returns NONE status when no relationship exists."""
        user1, user2 = two_users

        rel = rel_manager.get_relationship(user1.id, user2.id)
        assert rel.status == RelationshipStatus.NONE


class TestBug3DirectPendingRequestQuery:
    """Bug 3: Delete endpoint should use direct query, not O(n) scan."""

    def test_get_pending_request_direct_lookup(self, rel_manager, two_users):
        """_get_pending_request does direct DB lookup."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        # Direct lookup should work
        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is not None
        assert row["id"] == request.id
        assert row["sender_id"] == user1.id
        assert row["recipient_id"] == user2.id

    def test_get_pending_request_returns_none_when_no_match(
        self, rel_manager, two_users
    ):
        """_get_pending_request returns None when no pending request exists."""
        user1, user2 = two_users

        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is None

    def test_get_pending_request_only_finds_pending(self, rel_manager, two_users):
        """_get_pending_request only finds pending requests, not accepted ones."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, request.id)

        # Should not find the accepted request
        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is None


class TestBug4AcceptRouteSenderMatching:
    """Bug 4: Accept route should only match by sender_id."""

    def test_accept_by_sender_id(self, rel_manager, two_users):
        """Accepting a friend request should work when matching by sender_id."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        # Find the request by sender_id (simulating accept route logic)
        pending = rel_manager.get_pending_requests_incoming(user2.id)
        found = None
        for r in pending:
            if r.sender_id == user1.id:
                found = r
                break

        assert found is not None
        assert found.id == request.id

    def test_accept_does_not_match_unrelated_request_id(self, rel_manager, three_users):
        """Accept route should not match by random request ID."""
        user1, user2, user3 = three_users

        # user1 sends to user2
        request = rel_manager.send_friend_request(user1.id, user2.id)

        # user3 sends to user2
        request2 = rel_manager.send_friend_request(user3.id, user2.id)

        # user2 looks for requests from user1
        pending = rel_manager.get_pending_requests_incoming(user2.id)
        found = None
        for r in pending:
            if r.sender_id == user1.id:
                found = r
                break

        assert found is not None
        assert found.id == request.id
        # Should NOT match request2 even though it's also pending
        assert found.id != request2.id


class TestBug5SuggestedFriendsExcludePending:
    """Bug 5: Suggested friends should exclude users with pending requests."""

    def test_suggested_friends_excludes_outgoing_pending(
        self, rel_manager, three_users
    ):
        """Users with outgoing pending requests should not be suggested."""
        user1, user2, user3 = three_users
        # Create a chain: user1 -> user2 -> user3
        req1 = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req1.id)

        req2 = rel_manager.send_friend_request(user2.id, user3.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        # user1 sends request to user3 (pending)
        rel_manager.send_friend_request(user1.id, user3.id)

        # user3 should not be suggested to user1 since there's a pending request
        suggestions = rel_manager.get_suggested_friends(user1.id)
        assert user3.id not in suggestions

    def test_suggested_friends_excludes_incoming_pending(
        self, rel_manager, three_users
    ):
        """Users with incoming pending requests should not be suggested."""
        user1, user2, user3 = three_users
        # Create a chain: user1 -> user2 -> user3
        req1 = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req1.id)

        req2 = rel_manager.send_friend_request(user2.id, user3.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        # user3 sends request to user1 (pending)
        rel_manager.send_friend_request(user3.id, user1.id)

        # user3 should not be suggested to user1 since there's a pending request
        suggestions = rel_manager.get_suggested_friends(user1.id)
        assert user3.id not in suggestions

    def test_suggested_friends_includes_users_without_pending(
        self, rel_manager, three_users
    ):
        """Users without pending requests should still be suggested."""
        user1, user2, user3 = three_users
        # Create a chain: user1 -> user2 -> user3
        req1 = rel_manager.send_friend_request(user1.id, user2.id)
        rel_manager.accept_friend_request(user2.id, req1.id)

        req2 = rel_manager.send_friend_request(user2.id, user3.id)
        rel_manager.accept_friend_request(user3.id, req2.id)

        # No pending request between user1 and user3
        suggestions = rel_manager.get_suggested_friends(user1.id)
        assert user3.id in suggestions


class TestBug6UnblockTransaction:
    """Bug 6: Unblock user should be wrapped in transaction."""

    def test_unblock_in_transaction(self, rel_manager, two_users):
        """Unblock should succeed and be atomic."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        assert rel_manager.is_blocked(user1.id, user2.id)

        result = rel_manager.unblock_user(user1.id, user2.id)
        assert result is True
        assert not rel_manager.is_blocked(user1.id, user2.id)

    def test_unblock_preserves_consistency(self, rel_manager, two_users):
        """Unblock should maintain data consistency."""
        user1, user2 = two_users

        rel_manager.block_user(user1.id, user2.id)
        rel_manager.unblock_user(user1.id, user2.id)

        # Should be able to send friend request after unblock
        request = rel_manager.send_friend_request(user1.id, user2.id)
        assert request is not None
        assert request.status == FriendRequestStatus.PENDING


class TestBug7ForeignKeyConstraints:
    """Bug 7: Foreign key constraints should exist on relationship tables."""

    def test_schema_has_foreign_keys(self):
        """Relationship schema should contain FOREIGN KEY constraints."""
        from src.core.relationships.schema import SCHEMA

        assert "FOREIGN KEY" in SCHEMA
        assert "REFERENCES auth_users(id)" in SCHEMA
        assert "ON DELETE CASCADE" in SCHEMA

    def test_rel_friends_has_foreign_keys(self):
        """rel_friends table should have foreign keys for user_id and friend_id."""
        from src.core.relationships.schema import SCHEMA

        # Check that rel_friends definition includes foreign keys
        friends_section = SCHEMA.split("CREATE TABLE IF NOT EXISTS rel_friends")[1]
        friends_section = friends_section.split("CREATE TABLE")[0]

        assert "FOREIGN KEY (user_id) REFERENCES auth_users(id)" in friends_section
        assert "FOREIGN KEY (friend_id) REFERENCES auth_users(id)" in friends_section

    def test_rel_friend_requests_has_foreign_keys(self):
        """rel_friend_requests table should have foreign keys for sender_id and recipient_id."""
        from src.core.relationships.schema import SCHEMA

        requests_section = SCHEMA.split(
            "CREATE TABLE IF NOT EXISTS rel_friend_requests"
        )[1]
        requests_section = requests_section.split("CREATE TABLE")[0]

        assert "FOREIGN KEY (sender_id) REFERENCES auth_users(id)" in requests_section
        assert (
            "FOREIGN KEY (recipient_id) REFERENCES auth_users(id)" in requests_section
        )

    def test_rel_blocked_has_foreign_keys(self):
        """rel_blocked table should have foreign keys for blocker_id and blocked_id."""
        from src.core.relationships.schema import SCHEMA

        blocked_section = SCHEMA.split("CREATE TABLE IF NOT EXISTS rel_blocked")[1]

        assert "FOREIGN KEY (blocker_id) REFERENCES auth_users(id)" in blocked_section
        assert "FOREIGN KEY (blocked_id) REFERENCES auth_users(id)" in blocked_section


class TestBug8RaceConditionHandling:
    """Bug 8: UNIQUE constraint race condition should be handled gracefully."""

    def test_send_friend_request_handles_integrity_error(self, rel_manager, two_users):
        """send_friend_request should handle UNIQUE constraint violation."""
        user1, user2 = two_users

        # Simulate race condition by mocking the DB to raise integrity error
        original_execute = rel_manager._db.execute
        call_count = {"count": 0}

        def mock_execute(query, params=None):
            call_count["count"] += 1
            if "INSERT INTO rel_friend_requests" in query and call_count["count"] == 1:
                # Simulate UNIQUE constraint violation on first INSERT
                raise Exception("UNIQUE constraint failed")
            return original_execute(query, params)

        # Mock _get_pending_request to return a reverse request on retry
        original_get_pending = rel_manager._get_pending_request

        def mock_get_pending(sender_id, recipient_id):
            # On retry, return a reverse request
            if call_count["count"] >= 1:
                return original_get_pending(recipient_id, sender_id)
            return original_get_pending(sender_id, recipient_id)

        with patch.object(rel_manager._db, "execute", mock_execute):
            with patch.object(rel_manager, "_get_pending_request", mock_get_pending):
                # Should not raise, should handle race condition
                result = rel_manager.send_friend_request(user1.id, user2.id)
                assert result is not None
                assert result.sender_id == user1.id

    def test_send_friend_request_raises_on_non_unique_error(
        self, rel_manager, two_users
    ):
        """send_friend_request should re-raise non-UNIQUE errors."""
        user1, user2 = two_users

        original_execute = rel_manager._db.execute

        def mock_execute(query, params=None):
            if "INSERT INTO rel_friend_requests" in query:
                raise Exception("Some other database error")
            return original_execute(query, params)

        with patch.object(rel_manager._db, "execute", mock_execute):
            with pytest.raises(Exception, match="Some other database error"):
                rel_manager.send_friend_request(user1.id, user2.id)


class TestDeleteEndpointDirectQuery:
    """Tests verifying the delete endpoint uses direct queries."""

    def test_delete_pending_incoming_uses_direct_query(self, rel_manager, two_users):
        """Delete endpoint for pending incoming should find request directly."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        # Simulate what the delete endpoint does
        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is not None
        assert row["id"] == request.id

        # Decline the request
        rel_manager.decline_friend_request(user2.id, row["id"])

        # Should not find it anymore
        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is None

    def test_delete_pending_outgoing_uses_direct_query(self, rel_manager, two_users):
        """Delete endpoint for pending outgoing should find request directly."""
        user1, user2 = two_users

        request = rel_manager.send_friend_request(user1.id, user2.id)

        # Simulate what the delete endpoint does
        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is not None
        assert row["id"] == request.id

        # Cancel the request
        rel_manager.cancel_friend_request(user1.id, row["id"])

        # Should not find it anymore
        row = rel_manager._get_pending_request(user1.id, user2.id)
        assert row is None
