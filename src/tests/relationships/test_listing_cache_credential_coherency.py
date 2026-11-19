"""Cached relationship listing: credential isolation, live authz, coherency.

The ``GET /relationships/@me`` read is cached. Three contracts are pinned here:

1. A cache hit is byte-for-byte identical to a miss, and the warm request does
   no relationship-graph query at all (the query-count reduction the cache is
   there for).
2. Entries are partitioned by credential (``authenticated_cache_key``), so one
   user's private page can never be replayed to another, and a bot token can
   never replay its owner's page.
3. The bot-rejection gate runs on EVERY request *before* the cache lookup, so
   a warm (even poisoned) entry can never turn a would-be 403 into a 200.
4. Every mutation drops the cached page -- including the bot namespace -- so a
   friendship/block change is visible immediately instead of after the TTL.
"""

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.middleware.authentication import TokenInfo
from src.api.routes.relationships.base import RelationshipsBase
from src.api.routes.relationships.blocking import BlockingMixin
from src.api.routes.relationships.composer import RelationshipsRouter
from src.api.routes.relationships.deletion import DeletionMixin
from src.api.routes.relationships.friend_requests import FriendRequestMixin
from src.api.routes.relationships.listing import (
    _RELATIONSHIP_LIST_CACHE_PREFIX,
    _load_relationship_list,
)
from src.api.routes.users.helpers import authenticated_cache_key
from src.api.schemas.common import SnowflakeID
from src.core.auth.models import AccountType
from src.core.database.cache import decorators as cache_decorators
from src.core.database.cache import operations as cache_operations
from src.core.database.cache.base import (
    mem_cache_clear_pattern,
    mem_cache_get,
    mem_cache_set,
)


def _token(user_id, *, token_type="user", account_id=None):
    return TokenInfo(
        valid=True,
        token_type=token_type,
        account_id=SnowflakeID(str(account_id if account_id is not None else user_id)),
        user_id=SnowflakeID(str(user_id)),
        session_id=None,
        permissions={},
        rate_limit_tier="standard",
        expires_at=None,
        username="tester",
        account_type=AccountType.BOT if token_type == "bot" else AccountType.USER,
    )


def _friend(owner_id, target_id):
    # user_id is "me" (a SnowflakeID, like production) so the builder picks
    # friend_id as the visible target.
    return SimpleNamespace(
        user_id=SnowflakeID(str(owner_id)),
        friend_id=SnowflakeID(str(target_id)),
        created_at=1,
    )


class _FakeRelationships:
    def __init__(self, graph):
        self.graph = graph
        self.calls = []

    def get_all_relationships(self, uid):
        self.calls.append(int(uid))
        return self.graph.get(
            int(uid),
            {
                "friends": [],
                "pending_incoming": [],
                "pending_outgoing": [],
                "blocked": [],
            },
        )


class _FakeAuth:
    def get_user_profiles_bulk(self, ids):
        return {
            str(uid): {"username": f"user-{uid}", "avatar_url": None} for uid in ids
        }


class _FakePresence:
    def get_visible_presences_bulk(self, viewer, ids):
        return {uid: SimpleNamespace(status="online") for uid in ids}


@pytest.fixture
def listing_env(monkeypatch):
    """Run the real cached route against the in-memory cache tier only.

    Forcing ``get_client()`` to None keeps the assertions deterministic and
    independent of a running Valkey instance while exercising the exact @cached
    decorator and invalidation code used in production.
    """
    monkeypatch.setattr(cache_decorators, "get_client", lambda: None)
    monkeypatch.setattr(cache_operations, "get_client", lambda: None)
    mem_cache_clear_pattern(f"{_RELATIONSHIP_LIST_CACHE_PREFIX}:*")

    rel = _FakeRelationships(
        {
            100: {
                "friends": [_friend(100, 101)],
                "pending_incoming": [],
                "pending_outgoing": [],
                "blocked": [],
            },
            200: {
                "friends": [_friend(200, 201)],
                "pending_incoming": [],
                "pending_outgoing": [],
                "blocked": [],
            },
        }
    )
    monkeypatch.setattr(api, "get_relationships", lambda: rel)
    monkeypatch.setattr(api, "get_auth", lambda: _FakeAuth())
    monkeypatch.setattr(api, "get_presence", lambda: _FakePresence())

    yield rel

    mem_cache_clear_pattern(f"{_RELATIONSHIP_LIST_CACHE_PREFIX}:*")


def _call_listing(token, **kwargs):
    return RelationshipsRouter().get_relationships(current_user=token, **kwargs)


def _payload(items):
    return [item.model_dump(mode="json") for item in items]


def test_cache_hit_matches_miss_and_eliminates_graph_query(listing_env):
    """Warm response is identical and skips the relationship-graph read."""
    rel = listing_env
    cold = _call_listing(_token(100), limit=100, offset=0)
    cold_calls = list(rel.calls)
    assert cold_calls == [100]  # one cold graph read

    warm = _call_listing(_token(100), limit=100, offset=0)

    # Identical output, and no second query: 1 -> 0 graph reads on the hit.
    assert _payload(warm) == _payload(cold)
    assert rel.calls == cold_calls
    assert len(rel.calls) == 1


def test_no_cross_user_leak(listing_env):
    """Two authenticated users each get only their own relationship page."""
    rel = listing_env
    alice = _call_listing(_token(100), limit=100, offset=0)
    bob = _call_listing(_token(200), limit=100, offset=0)

    assert [item.user_id for item in alice] == ["101"]
    assert [item.user_id for item in bob] == ["201"]
    assert rel.calls == [100, 200]

    # Re-requests are served from each credential's own entry.
    assert [
        item.user_id for item in _call_listing(_token(100), limit=100, offset=0)
    ] == ["101"]
    assert rel.calls == [100, 200]


def test_bot_key_partitioned_from_owner(listing_env):
    """A bot credential can never land on its owner's cache key."""
    builder = authenticated_cache_key(_RELATIONSHIP_LIST_CACHE_PREFIX)
    owner = _token(100)
    bot = _token(100, token_type="bot", account_id=500)
    owner_key = builder(current_user=owner, limit=100, offset=0)
    bot_key = builder(current_user=bot, limit=100, offset=0)

    assert owner_key != bot_key
    assert ":bot:500:100:" in bot_key
    # The cached loader actually uses this builder.
    builder_cell = _key_builder(_load_relationship_list)
    assert builder_cell(100) != builder_cell(500, bot=True)


def _key_builder(func):
    """Return a callable building user/bot keys with the wrapper's builder."""
    captured = None
    for name, cell in zip(func.__code__.co_freevars, func.__closure__ or ()):
        if name == "key_builder":
            captured = cell.cell_contents
    assert captured is not None, "cached loader lost its key_builder"

    def build(uid, *, bot=False):
        token = _token(
            uid,
            token_type="bot" if bot else "user",
            account_id=(uid + 400 if bot else uid),
        )
        return captured(current_user=token, limit=100, offset=0)

    return build


def test_bot_denied_before_cache_even_with_poisoned_entry(listing_env):
    """A warm/poisoned bot entry can never turn the authz denial into a hit."""
    rel = listing_env
    bot = _token(100, token_type="bot", account_id=500)
    bot_key = _key_builder(_load_relationship_list)(100, bot=True)

    # Attacker pre-seeds the bot-partitioned key with an arbitrary private page.
    mem_cache_set(bot_key, [{"user_id": "666", "status": "friend"}], 60)

    with pytest.raises(HTTPException) as exc:
        _call_listing(bot, limit=100, offset=0)

    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == 403
    # Cold path never ran: the denial happened before any cache read/build.
    assert rel.calls == []


def test_denial_does_not_create_a_cache_entry(listing_env):
    """A rejected credential neither reads nor populates the cache."""
    rel = listing_env
    bot = _token(100, token_type="bot", account_id=500)
    bot_key = _key_builder(_load_relationship_list)(100, bot=True)
    mem_cache_clear_pattern(bot_key)

    with pytest.raises(HTTPException):
        _call_listing(bot, limit=100, offset=0)

    assert mem_cache_get(bot_key) is None
    assert rel.calls == []


def test_mutation_invalidation_refreshes_cached_listing(listing_env):
    """After a mutation drops the cache, the next read reflects new data."""
    rel = listing_env
    first = _call_listing(_token(100), limit=100, offset=0)
    assert [item.user_id for item in first] == ["101"]

    # A relationship mutation changes the underlying graph and invalidates.
    rel.graph[100]["friends"] = [_friend(100, 102)]
    RelationshipsBase._invalidate_relationship_list_cache(None, 100)

    refreshed = _call_listing(_token(100), limit=100, offset=0)
    assert [item.user_id for item in refreshed] == ["102"]
    # Both reads actually hit the graph (no stale warm page survived).
    assert rel.calls == [100, 100]


def test_all_mutations_invalidate_and_globs_match_cached_keys(listing_env):
    """Every mutation path invalidates; its globs hit user AND bot keys."""
    # Static guard: all four mutation handlers must call the invalidation
    # helper (an omitted call on any one of them used to leave stale pages).
    for handler in (
        FriendRequestMixin.create_relationship,
        FriendRequestMixin.accept_friend_request,
        BlockingMixin.block_user,
        DeletionMixin.delete_relationship,
    ):
        assert "_invalidate_relationship_list_cache" in inspect.getsource(handler), (
            f"{handler.__name__} does not invalidate the cached listing"
        )

    builder = _key_builder(_load_relationship_list)
    user_key = builder(100)
    bot_key = builder(100, bot=True)
    other_key = builder(200)

    for key in (user_key, bot_key, other_key):
        mem_cache_set(key, [{"poison": True}], 60)
        assert mem_cache_get(key) is not None

    # A single mutation by user 100 drops both credential partitions for that
    # account, and leaves another user's page intact.
    RelationshipsBase._invalidate_relationship_list_cache(None, 100)

    assert mem_cache_get(user_key) is None
    assert mem_cache_get(bot_key) is None
    assert mem_cache_get(other_key) == [{"poison": True}]
