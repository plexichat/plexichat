"""
Regression tests for relationship cache invalidation (Bug R1).

The relationship listing route ``GET /relationships/@me`` is cached for 60s
under the ``relationships_api`` prefix. Mutations (send/accept/block/remove)
call ``RelationshipsBase._invalidate_relationship_list_cache`` which builds a
glob and calls ``invalidate_pattern``.

Historically the glob was ``relationships_api:*:current_user:*:{uid}`` which
could never match the real cache key produced by ``generate_cache_key``
(because the key carries trailing ``:limit:...:offset:...`` segments and the
``TokenInfo:`` segment between ``current_user:`` and the uid). As a result the
list cache was never invalidated and stayed stale for up to 60s.

These tests pin the contract between the generated key and the invalidation
glob so the regression cannot silently return.
"""

import fnmatch

from src.core.database.cache.serialization import generate_cache_key


def _fake_token_info(user_id: int):
    # Mirror the shape ``generate_cache_key`` inspects: class name "TokenInfo"
    # and a ``user_id`` attribute.
    return type("TokenInfo", (), {"user_id": user_id})()


def _build_listing_cache_key(user_id: int, limit: int = 100, offset: int = 0) -> str:
    # Same arguments/kwargs the FastAPI route is invoked with. ``self`` (the
    # router instance) is the first positional arg.
    router = object()
    return generate_cache_key(
        "relationships_api",
        router,
        current_user=_fake_token_info(user_id),
        limit=limit,
        offset=offset,
    )


def test_listing_cache_key_shape():
    key = _build_listing_cache_key(42)
    assert key.startswith("relationships_api:")
    # The real key always ends with the limit/offset segments.
    assert key.endswith(":limit:int:100:offset:int:0")
    assert ":current_user:TokenInfo:42:" in key


def test_new_invalidation_glob_matches_key():
    key = _build_listing_cache_key(42)
    new_pattern = "relationships_api:*:current_user:TokenInfo:42:*"
    assert fnmatch.fnmatch(key, new_pattern)


def test_old_broken_glob_does_not_match_key():
    key = _build_listing_cache_key(42)
    old_pattern = "relationships_api:*:current_user:*:{0}".format(42)
    # The whole point of the bug: this never matched, so invalidation was a
    # silent no-op. Assert it really does NOT match so the regression is real.
    assert not fnmatch.fnmatch(key, old_pattern)


def test_invalidation_glob_is_per_user():
    key_other = _build_listing_cache_key(999)
    new_pattern_for_42 = "relationships_api:*:current_user:TokenInfo:42:*"
    # Another user's key must not be invalidated by user 42's glob.
    assert not fnmatch.fnmatch(key_other, new_pattern_for_42)
