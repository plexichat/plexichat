"""
Tests for audit bug fixes in the relationships system.

Covers:
- REL-1: relationship list cache-invalidation pattern matches generated keys
- REL-3: friend-request message length is enforced
"""

from typing import Any, cast

import fnmatch

import pytest

from src.core.relationships.exceptions import RelationshipValidationError
from src.api.routes.relationships.base import RelationshipsBase


class TestRel1CacheInvalidationPattern:
    """REL-1: the invalidation glob must match the generated cache keys."""

    def test_invalidation_patterns_match_real_key_shapes(self, monkeypatch):
        """Globs must match the keys the cached loader actually generates.

        The @cached decorator drops the bound ``self`` from the key, so real
        listing keys have no router-instance segment. A leading ``*:`` (the
        old shape) therefore never matched and left listings stale. Bot
        credentials live in their own namespace and must be dropped too.
        """
        import src.api.routes.relationships.base as base_mod

        captured = {"patterns": []}

        def fake_invalidate(pattern):
            captured["patterns"].append(pattern)

        # base.py does `from src.core.database import invalidate_pattern`, so the
        # name is bound in base_mod's namespace and must be patched there.
        monkeypatch.setattr(base_mod, "invalidate_pattern", fake_invalidate)

        # _invalidate_relationship_list_cache is a plain method; self is unused.
        RelationshipsBase._invalidate_relationship_list_cache(cast(Any, None), 1, 2)

        # Presence reads cache the viewer-specific visible body, so a
        # relationship mutation drops them before the listing entries. Each
        # user gets a user-token glob and a bot-namespace glob.
        assert captured["patterns"] == [
            "presence_read_api:*",
            "relationships_api:current_user:TokenInfo:1:*",
            "relationships_api:bot:*:1:*",
            "relationships_api:current_user:TokenInfo:2:*",
            "relationships_api:bot:*:2:*",
        ]
        assert all(p.startswith("relationships_api:") for p in captured["patterns"][1:])

    def test_pattern_matches_generated_listing_key(self):
        """The glob matches the *real* cache key produced by the loader.

        The @cached decorator drops the bound ``self``, so the key is::

            relationships_api:current_user:TokenInfo:<uid>:limit:int:100:offset:int:0

        There is no router-instance segment. The glob
        ``relationships_api:current_user:TokenInfo:<uid>:*`` matches it; the
        previous globs (missing ``TokenInfo:``/trailing ``*``, or assuming a
        router segment via a leading ``*:``) never matched, so invalidation
        was a silent no-op.
        """
        from src.api.routes.users.helpers import authenticated_cache_key
        from src.api.routes.relationships.listing import (
            _RELATIONSHIP_LIST_CACHE_PREFIX,
        )

        token = type(
            "TokenInfo", (), {"user_id": 42, "token_type": "user", "account_id": 42}
        )()
        generated_key = authenticated_cache_key(_RELATIONSHIP_LIST_CACHE_PREFIX)(
            current_user=token, limit=100, offset=0
        )
        assert fnmatch.fnmatch(
            generated_key, "relationships_api:current_user:TokenInfo:42:*"
        )
        assert not fnmatch.fnmatch(
            generated_key, "relationships_api:*:current_user:TokenInfo:42:*"
        )
        assert not fnmatch.fnmatch(
            generated_key, "relationships_api:*:current_user:*:42"
        )


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
