"""Query-count guard for ``GET /relationships/@me``.

Hydration must stay proportional to the *page*, not to the size of the
relationship graph: user profiles and visible presences are fetched in one
bulk call each. A regression back to per-friend lookups blows this budget as
soon as the graph holds more than a couple of users.
"""
from typing import Any, cast

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import uuid

import src.api as api
from src.api.routes.relationships.listing import ListingMixin
from src.core.database.cache.base import mem_cache_clear_pattern
from src.utils import encryption


def _cold(db):
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


@pytest.mark.relationships
def test_listing_hydration_query_count_is_bounded(
    db, auth_manager, rel_manager, presence_manager, three_users, monkeypatch
):
    me, *_others = three_users
    # Grow the graph well past the page so a per-friend lookup would show up.
    friends = list(three_users[1:])
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        for i in range(4):
            friends.append(
                auth_manager.register(
                    username=f"budget_friend_{uuid.uuid4().hex[:8]}",
                    email=f"budget_{uuid.uuid4().hex[:8]}@example.com",
                    password="TestPass123!",
                )
            )
    for other in friends:
        req = rel_manager.send_friend_request(me.id, other.id)
        rel_manager.accept_friend_request(other.id, req.id)

    monkeypatch.setattr(api, "get_relationships", lambda: rel_manager)
    monkeypatch.setattr(api, "get_auth", lambda: auth_manager)
    monkeypatch.setattr(api, "get_presence", lambda: presence_manager)

    _cold(db)
    with (
        patch.object(
            rel_manager, "get_blocked_user_ids", wraps=rel_manager.get_blocked_user_ids
        ) as own_blocked,
        patch.object(
            rel_manager, "get_all_blocked_ids", wraps=rel_manager.get_all_blocked_ids
        ) as all_blocked,
    ):
        result = cast(Any, ListingMixin.get_relationships).__wrapped__(
            ListingMixin(),
            current_user=SimpleNamespace(user_id=me.id),
            limit=100,
            offset=0,
        )

    # Presence hydration resolves the two-way block set with one lookup.
    all_blocked.assert_called_once_with(me.id)
    own_blocked.assert_not_called()

    count = db.get_request_metrics()["query_count"]
    assert len(result) == len(friends)
    assert count <= 10, f"relationship listing issued {count} queries, budget is 10"
