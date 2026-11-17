"""Query-count and authorization guards for bulk presence reads.

``get_visible_presences_bulk`` feeds the cached relationship listing and DM
hydration paths. It must resolve the viewer's two-way block set with a single
(cacheable) lookup: the historical implementation fetched both the viewer's own
block list and the full two-way union, spending an extra round trip on a cold
cache while computing a set whose only use was to be re-joined with the same
"is blocked" predicate. Blocking must stay symmetric — either direction must
render the counterpart offline.
"""

from unittest.mock import patch

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern
from src.core.presence.models import UserStatus


def _cold(db):
    """Start from a cold cache so the measurement cannot hide extra queries."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


@pytest.mark.presence
def test_bulk_presence_uses_one_blocked_set_lookup(
    db, presence_manager, rel_manager, three_users
):
    user1, user2, user3 = three_users
    presence_manager.set_status(user1.id, UserStatus.ONLINE)
    presence_manager.set_status(user2.id, UserStatus.ONLINE)
    presence_manager.set_status(user3.id, UserStatus.ONLINE)

    _cold(db)

    with (
        patch.object(
            rel_manager,
            "get_blocked_user_ids",
            wraps=rel_manager.get_blocked_user_ids,
        ) as own_blocked,
        patch.object(
            rel_manager,
            "get_all_blocked_ids",
            wraps=rel_manager.get_all_blocked_ids,
        ) as all_blocked,
    ):
        result = presence_manager.get_visible_presences_bulk(
            user1.id, [user2.id, user3.id]
        )

    # Exactly one two-way union lookup, never the redundant one-way lookup.
    all_blocked.assert_called_once_with(user1.id)
    own_blocked.assert_not_called()
    assert set(result) == {user2.id, user3.id}

    count = db.get_request_metrics()["query_count"]
    assert count <= 5, f"bulk presence issued {count} queries, budget is 5"


@pytest.mark.presence
def test_bulk_presence_blocking_is_symmetric(
    db, presence_manager, rel_manager, two_users
):
    user1, user2 = two_users
    presence_manager.set_status(user1.id, UserStatus.ONLINE)
    presence_manager.set_status(user2.id, UserStatus.ONLINE)

    # user1 blocks user2: user2 must see user1 offline.
    rel_manager.block_user(user1.id, user2.id)
    result = presence_manager.get_visible_presences_bulk(user2.id, [user1.id])
    assert result[user1.id].status == UserStatus.OFFLINE

    # The opposite direction must render offline too.
    rel_manager.unblock_user(user1.id, user2.id)
    rel_manager.block_user(user2.id, user1.id)
    result = presence_manager.get_visible_presences_bulk(user2.id, [user1.id])
    assert result[user1.id].status == UserStatus.OFFLINE
