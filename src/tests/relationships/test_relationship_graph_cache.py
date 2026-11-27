"""Regression tests for the cached aggregate relationship graph.

The manager's ``_invalidate_all_relationships_cache`` used to call
``invalidate_pattern`` (a Valkey ``KEYS`` scan of the whole keyspace) after
every relationship mutation. The cache key produced by ``get_all_relationships``
is deterministic, so a targeted delete is both cheaper and more precise.
"""

from typing import Any, cast

import pytest

from src.core.relationships.manager.helpers import RelationshipHelpersMixin


class TestTargetedAggregateInvalidation:
    def test_uses_targeted_delete_not_pattern_scan(self, monkeypatch):
        import src.core.database as db_mod

        deleted = []
        patterns = []
        monkeypatch.setattr(
            db_mod, "invalidate_cached", lambda key: deleted.append(key)
        )
        monkeypatch.setattr(
            db_mod, "invalidate_pattern", lambda pattern: patterns.append(pattern)
        )

        RelationshipHelpersMixin._invalidate_all_relationships_cache(
            cast(Any, None), 1, 2, 1
        )

        # Both derived caches are evicted per affected user, and the duplicates
        # in the argument list collapse: the aggregate graph and the presence
        # fan-out audience are equally stale after a block/friend change.
        assert deleted == [
            "all_relationships:int:1",
            "presence_targets:int:1",
            "all_relationships:int:2",
            "presence_targets:int:2",
        ]
        assert patterns == []

    def test_deletes_the_exact_cached_key(self):
        from src.core.database.cache.serialization import generate_cache_key

        key = generate_cache_key("all_relationships", 42)
        assert key == "all_relationships:int:42"


@pytest.mark.relationships
class TestAggregateInvalidationStillWorks:
    def test_send_then_refresh_drops_pending(self, rel_manager, two_users):
        user1, user2 = two_users
        req = rel_manager.send_friend_request(user1.id, user2.id)

        before = rel_manager.get_all_relationships(user2.id)
        assert any(item.id == req.id for item in before["pending_incoming"])

        rel_manager.decline_friend_request(user2.id, req.id)

        after = rel_manager.get_all_relationships(user2.id)
        assert not any(item.id == req.id for item in after["pending_incoming"])
