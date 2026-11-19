"""
Regression tests for relationship cache invalidation (Bug R1).

The relationship listing route ``GET /relationships/@me`` is cached under the
``relationships_api`` prefix. Mutations (send/accept/block/remove) call
``RelationshipsBase._invalidate_relationship_list_cache`` which builds globs
and calls ``invalidate_pattern``.

Two historically broken contracts are pinned here:

1. The glob used to be ``relationships_api:*:current_user:*:{uid}`` -- it could
   never match the generated key (which carries ``TokenInfo:`` and trailing
   ``limit``/``offset`` segments).

2. When that was corrected to ``relationships_api:*:current_user:TokenInfo:{uid}:*``
   the leading ``*:`` was still wrong: the @cached decorator drops the bound
   ``self`` from the key, so real keys have NO router-instance segment. The
   glob silently matched nothing and listings stayed stale for the full TTL.

3. Bot credentials get a dedicated namespace; invalidation must drop those
   entries too, otherwise a stale bot-partitioned page survives a mutation.

These tests pin the contract between the *real* generated keys (built with the
same ``authenticated_cache_key`` the route uses) and the invalidation globs.
"""

import fnmatch

from src.api.routes.users.helpers import authenticated_cache_key
from src.api.routes.relationships.listing import _RELATIONSHIP_LIST_CACHE_PREFIX


def _fake_token_info(user_id: int, *, token_type: str = "user", account_id: int = 0):
    # Mirror the shape ``generate_cache_key`` / ``authenticated_cache_key``
    # inspect: class name "TokenInfo" with ``user_id``/``token_type``/``account_id``.
    return type(
        "TokenInfo",
        (),
        {
            "user_id": user_id,
            "token_type": token_type,
            "account_id": account_id,
        },
    )()


def _build_listing_cache_key(user_id: int, limit: int = 100, offset: int = 0) -> str:
    # The cached loader is a module-level function called with kwargs only;
    # there is no router/self segment in the real key.
    builder = authenticated_cache_key(_RELATIONSHIP_LIST_CACHE_PREFIX)
    return builder(current_user=_fake_token_info(user_id), limit=limit, offset=offset)


def _build_bot_listing_cache_key(
    owner_id: int, bot_id: int, limit: int = 100, offset: int = 0
) -> str:
    builder = authenticated_cache_key(_RELATIONSHIP_LIST_CACHE_PREFIX)
    return builder(
        current_user=_fake_token_info(owner_id, token_type="bot", account_id=bot_id),
        limit=limit,
        offset=offset,
    )


def test_listing_cache_key_shape():
    key = _build_listing_cache_key(42)
    assert key.startswith("relationships_api:")
    # The real key always ends with the limit/offset segments.
    assert key.endswith(":limit:int:100:offset:int:0")
    assert ":current_user:TokenInfo:42:" in key
    # No router-instance segment: the decorator drops ``self``.
    assert "SimpleNamespace" not in key and "RelationshipsRouter" not in key


def test_new_invalidation_glob_matches_key():
    key = _build_listing_cache_key(42)
    new_pattern = "relationships_api:current_user:TokenInfo:42:*"
    assert fnmatch.fnmatch(key, new_pattern)


def test_old_broken_globs_do_not_match_key():
    key = _build_listing_cache_key(42)
    # The original bug: missing ``TokenInfo:`` and the trailing ``*``.
    old_pattern = "relationships_api:*:current_user:*:{0}".format(42)
    # The second bug: a leading ``*:`` assumes a router segment that is never
    # present in the real key, so it silently matched nothing.
    router_pattern = "relationships_api:*:current_user:TokenInfo:42:*"
    assert not fnmatch.fnmatch(key, old_pattern)
    assert not fnmatch.fnmatch(key, router_pattern)


def test_invalidation_glob_is_per_user():
    key_other = _build_listing_cache_key(999)
    new_pattern_for_42 = "relationships_api:current_user:TokenInfo:42:*"
    # Another user's key must not be invalidated by user 42's glob.
    assert not fnmatch.fnmatch(key_other, new_pattern_for_42)


def test_bot_key_partitioned_and_invalidated_for_owner():
    key = _build_bot_listing_cache_key(owner_id=42, bot_id=500)
    owner_key = _build_listing_cache_key(42)
    # A bot never lands on its owner's (or a user's) key.
    assert key != owner_key
    assert ":bot:500:42:" in key
    # Its owner's mutation still drops the stale bot-partitioned entry.
    assert fnmatch.fnmatch(key, "relationships_api:bot:*:42:*")
    assert not fnmatch.fnmatch(key, "relationships_api:bot:*:999:*")
