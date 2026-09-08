"""Regression tests for relationship cluster fixes.

Covers:
- ``assert result is not None`` control flow replaced with real raises (L3):
  a failed re-fetch must raise a domain error, not return None to serialization
  under ``python -O``.
- Auto-accept inside ``send_friend_request`` invalidates the cache explicitly
  instead of relying on ``accept_friend_request``'s internals (L4).
- Block-status oracle closed (M3): a friend request to a user who blocked
  the sender is indistinguishable from a request to a nonexistent user.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.core.relationships.exceptions import (
    FriendRequestNotFoundError,
    RelationshipValidationError,
    UserBlockedError,
    UserNotFoundError,
)


@pytest.mark.relationships
class TestNoBareAsserts:
    """L3: refetch failures raise instead of relying on assert (stripped by -O)."""

    def test_send_friend_request_refetch_failure_raises(self, rel_manager, two_users):
        user1, user2 = two_users
        with patch.object(rel_manager, "get_friend_request", return_value=None):
            with pytest.raises(FriendRequestNotFoundError):
                rel_manager.send_friend_request(user1.id, user2.id, "hello")

    def test_accept_refetch_failure_raises(self, rel_manager, two_users):
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id, "hi")
        with patch.object(rel_manager, "get_friend_request", return_value=None):
            with pytest.raises(FriendRequestNotFoundError):
                rel_manager.accept_friend_request(user2.id, req.id)

    def test_block_refetch_failure_raises(self, rel_manager, two_users):
        user1, user2 = two_users
        with patch.object(rel_manager, "get_block", return_value=None):
            with pytest.raises(RelationshipValidationError):
                rel_manager.block_user(user1.id, user2.id)


@pytest.mark.relationships
class TestAutoAcceptInvalidatesCache:
    """L4: the auto-accept path must invalidate like every successful send."""

    def test_auto_accept_invalidates_both_users(self, rel_manager, two_users):
        user1, user2 = two_users
        # user2 -> user1 pending request exists; user1 sending to user2
        # auto-accepts it.
        rel_manager.send_friend_request(user2.id, user1.id, "first")

        calls = []
        original = rel_manager._invalidate_all_relationships_cache

        def spy(*args, **kwargs):
            calls.append(args)
            return original(*args, **kwargs)

        rel_manager._invalidate_all_relationships_cache = spy
        try:
            rel_manager.send_friend_request(user1.id, user2.id, "second")
        finally:
            rel_manager._invalidate_all_relationships_cache = original

        # The explicit sender->recipient invalidation from send_friend_request
        # must be present (accept's internal call is the reverse direction).
        assert (user1.id, user2.id) in calls


@pytest.mark.relationships
class TestBlockOracleM3:
    """M3: blocked-sender and missing-target friend requests are identical."""

    def _route_error(self, send_side_effect, target_id: int):
        """Run the POST friend-request route and capture its HTTP error."""
        from src.api.routes.relationships.friend_requests import FriendRequestMixin
        from src.api.schemas.relationships import FriendRequestCreate

        relationships = MagicMock()
        relationships.send_friend_request.side_effect = send_side_effect

        handler = FriendRequestMixin()
        handler._dispatch_relationship_event = AsyncMock()
        handler._invalidate_relationship_list_cache = MagicMock()

        body = FriendRequestCreate(user_id=target_id)
        current_user = SimpleNamespace(user_id=123456789)

        with (
            patch(
                "src.api.routes.relationships.friend_requests.api.get_relationships",
                return_value=relationships,
            ),
            patch(
                "src.api.routes.relationships.friend_requests.api.get_auth",
                return_value=None,
            ),
            patch(
                "src.api.routes.relationships.friend_requests."
                "_check_friend_request_rate_limit",
                return_value=None,
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(handler.create_relationship(body, current_user))
        return exc_info.value.status_code, exc_info.value.detail

    def test_blocked_and_missing_produce_same_response(self, rel_manager, two_users):
        """Route: blocked-by-target and nonexistent-target are identical."""
        user1, user2 = two_users
        rel_manager.block_user(user2.id, user1.id)
        blocked_target = user2.id
        missing_target = 999999999999

        # Sanity: the manager still records the true reason server-side via
        # the structured attributes (used only for logging, never the body).
        with pytest.raises(UserBlockedError) as blocked_exc:
            rel_manager.send_friend_request(user1.id, blocked_target)
        assert blocked_exc.value.blocked_by == blocked_target
        assert "blocked" not in str(blocked_exc.value).lower()

        blocked_status, blocked_detail = self._route_error(
            UserBlockedError(
                f"User {blocked_target} not found",
                blocked_by=blocked_target,
                blocked_user=user1.id,
            ),
            blocked_target,
        )
        missing_status, missing_detail = self._route_error(
            UserNotFoundError(f"User {missing_target} not found"),
            missing_target,
        )

        assert blocked_status == missing_status == 404
        assert blocked_detail == missing_detail
        assert blocked_detail == {"error": {"code": 404, "message": "User not found"}}

    def test_manager_blocked_message_matches_not_found(self, rel_manager, two_users):
        """Manager: blocked message must not reveal the block."""
        user1, user2 = two_users
        rel_manager.block_user(user2.id, user1.id)

        with pytest.raises(UserBlockedError) as blocked_exc:
            rel_manager.send_friend_request(user1.id, user2.id)
        with pytest.raises(UserNotFoundError) as missing_exc:
            rel_manager.send_friend_request(user1.id, 999999999999)

        assert str(blocked_exc.value) == f"User {user2.id} not found"
        assert str(missing_exc.value) == "User 999999999999 not found"
        assert "block" not in str(blocked_exc.value).lower()
