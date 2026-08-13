"""
Tests for audit bug fixes in the relationships system.

Covers:
- REL-1: relationship list cache-invalidation pattern matches generated keys
- REL-3: friend-request message length is enforced
"""

import fnmatch

import pytest

from src.core.relationships.exceptions import RelationshipValidationError
from src.api.routes.relationships.base import RelationshipsBase


class TestRel1CacheInvalidationPattern:
    """REL-1: the invalidation glob must match the generated cache keys."""

    def test_invalidation_pattern_wildcards_router_segment(self, monkeypatch):
        """The pattern must include a wildcard for the router-instance segment."""
        import src.api.routes.relationships.base as base_mod

        captured = {"patterns": []}

        def fake_invalidate(pattern):
            captured["patterns"].append(pattern)

        # base.py does `from src.core.database import invalidate_pattern`, so the
        # name is bound in base_mod's namespace and must be patched there.
        monkeypatch.setattr(base_mod, "invalidate_pattern", fake_invalidate)

        # _invalidate_relationship_list_cache is a plain method; self is unused.
        RelationshipsBase._invalidate_relationship_list_cache(None, 1, 2)

        assert captured["patterns"] == [
            "relationships_api:*:current_user:*:1",
            "relationships_api:*:current_user:*:2",
        ]
        assert all("*:current_user:*" in p for p in captured["patterns"])

    def test_pattern_matches_generated_listing_key(self):
        """The pattern matches the key format produced for a listing route.

        Listing route keys look like:
            relationships_api:<Router repr>:current_user:TokenInfo:<uid>
        The old pattern (without the leading wildcard) never matched.
        """
        generated_key = (
            "relationships_api:RelationshipsRouter:<...>:current_user:TokenInfo:42"
        )
        pattern = "relationships_api:*:current_user:*42"
        assert fnmatch.fnmatch(generated_key, pattern)


class TestRel3FriendRequestMessageLength:
    """REL-3: over-long friend-request messages must be rejected."""

    def test_message_too_long_raises(self, rel_manager, two_users):
        user1, user2 = two_users
        with pytest.raises(RelationshipValidationError):
            rel_manager.send_friend_request(user1.id, user2.id, message="x" * 257)

    def test_message_at_limit_accepted(self, rel_manager, two_users):
        user1, user2 = two_users
        # Exactly at the max length (256) must succeed.
        result = rel_manager.send_friend_request(user1.id, user2.id, message="y" * 256)
        assert result is not None
