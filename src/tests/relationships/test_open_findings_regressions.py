"""Regression tests for relationship cluster fixes.

Covers:
- ``assert result is not None`` control flow replaced with real raises (L3):
  a failed re-fetch must raise a domain error, not return None to serialization
  under ``python -O``.
- Auto-accept inside ``send_friend_request`` invalidates the cache explicitly
  instead of relying on ``accept_friend_request``'s internals (L4).
"""

from unittest.mock import patch

import pytest

from src.core.relationships.exceptions import (
    FriendRequestNotFoundError,
    RelationshipValidationError,
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
