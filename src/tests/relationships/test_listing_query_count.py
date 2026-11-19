"""Query-count guard for ``GET /relationships/@me``.

Hydration must stay proportional to the *page*, not to the size of the
relationship graph: user profiles and visible presences are fetched in one
bulk call each. A regression back to per-friend lookups blows this budget as
soon as the graph holds more than a couple of users.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import uuid

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes.relationships.composer import RelationshipsRouter
from src.api.routes.relationships.listing import _build_relationship_list
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType
from src.core.database.cache.base import mem_cache_clear_pattern
from src.utils import encryption


def _user_token(uid):
    return TokenInfo(
        valid=True,
        token_type="user",
        account_id=SnowflakeID(str(uid)),
        user_id=SnowflakeID(str(uid)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.USER,
    )


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
        result = _build_relationship_list(
            current_user=SimpleNamespace(user_id=me.id, token_type="user"),
            limit=100,
            offset=0,
        )

    # Presence hydration resolves the two-way block set with one lookup.
    all_blocked.assert_called_once_with(me.id)
    own_blocked.assert_not_called()

    count = db.get_request_metrics()["query_count"]
    assert len(result) == len(friends)
    assert count <= 10, f"relationship listing issued {count} queries, budget is 10"


@pytest.mark.relationships
def test_cached_listing_drops_db_queries_on_warm_read(
    db, auth_manager, rel_manager, presence_manager, three_users, monkeypatch
):
    """Measure the cache's query-count reduction through the real route.

    The first (cold) request pays the graph + hydration queries; the second,
    identical request is served entirely from the cache and issues zero DB
    queries. This is the reduction the ``relationships_api`` cache exists for.
    """
    me, other, *_ = three_users
    req = rel_manager.send_friend_request(me.id, other.id)
    rel_manager.accept_friend_request(other.id, req.id)

    monkeypatch.setattr(api, "get_relationships", lambda: rel_manager)
    monkeypatch.setattr(api, "get_auth", lambda: auth_manager)
    monkeypatch.setattr(api, "get_presence", lambda: presence_manager)

    _cold(db)
    router = RelationshipsRouter()
    token = _user_token(me.id)

    cold = router.get_relationships(current_user=token, limit=100, offset=0)
    cold_queries = db.get_request_metrics()["query_count"]
    assert cold_queries > 0

    db.reset_request_metrics()
    warm = router.get_relationships(current_user=token, limit=100, offset=0)
    warm_queries = db.get_request_metrics()["query_count"]

    assert [row.model_dump(mode="json") for row in warm] == [
        row.model_dump(mode="json") for row in cold
    ]
    assert warm_queries == 0, (
        f"warm cached listing still issued {warm_queries} DB queries "
        f"(cold={cold_queries})"
    )
