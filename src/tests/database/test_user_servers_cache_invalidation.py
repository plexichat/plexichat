"""Regression tests for per-user server-list cache invalidation.

A membership change (join/kick/leave/owner transfer) calls
``invalidate_user_servers(user_id)``. Historically that helper used the glob
``servers:*:<uid>*``, which never matched the real cache keys -- the manager
and API tiers both start with ``user_servers``/``user_servers_api`` -- so a
stale server list was served until TTL expiry.

These tests pin the contract between the generated cache keys and the
invalidation globs so the regression cannot silently return. They also guard
against over-broad patterns that would evict a different user whose id merely
contains the target id as a substring (42 vs 142).
"""

import fnmatch

import pytest

from src.core.database.cache import operations
from src.core.database.cache import session as session_cache
from src.core.database.cache.base import _mem_cache
from src.core.database.cache.serialization import generate_cache_key
from src.core.database.cache.session import invalidate_user_servers

USER = 42
# Contains ``USER`` as a substring -- catches over-broad ``*<uid>*`` globs.
OTHER_USER = 142


def _token_info(user_id: int):
    """Mirror the ``TokenInfo`` shape ``generate_cache_key`` inspects."""
    return type("TokenInfo", (), {"user_id": user_id})()


def _user_server_keys(user_id: int):
    """Every real cache key that describes ``user_id``'s server list."""
    return [
        # @cached(prefix="user_servers") on ServerManager.get_servers; ``self``
        # is dropped, leaving the typed int:<uid> segment and trailing kwargs.
        generate_cache_key("user_servers", user_id, limit=100),
        generate_cache_key("user_servers", user_id, limit=200),
        # @cached(prefix="user_servers_api") on the GET /servers route.
        generate_cache_key("user_servers_api", current_user=_token_info(user_id)),
        # Legacy plain key written directly by the membership helpers.
        f"user_servers:{user_id}",
    ]


@pytest.fixture(autouse=True)
def _offline_mem_only(monkeypatch):
    """Isolate the in-memory cache and force the offline (mem-only) tier."""
    _mem_cache.clear()
    monkeypatch.setattr(operations, "get_client", lambda: None)
    monkeypatch.setattr(operations, "is_available", lambda: False)
    monkeypatch.setattr(session_cache, "get_client", lambda: None)
    monkeypatch.setattr(session_cache, "is_available", lambda: False)
    yield
    _mem_cache.clear()


def test_real_user_server_key_shapes():
    """The keys under test are the ones the codebase actually generates."""
    keys = _user_server_keys(USER)
    assert "user_servers:int:42:limit:int:100" in keys
    assert "user_servers:int:42:limit:int:200" in keys
    assert "user_servers_api:current_user:TokenInfo:42" in keys
    assert "user_servers:42" in keys


def test_old_broken_glob_never_matched_real_keys():
    """Documents the bug: the previous pattern matched nothing."""
    old_pattern = f"servers:*:{USER}*"
    for key in _user_server_keys(USER):
        assert not fnmatch.fnmatch(key, old_pattern)


def test_invalidate_user_servers_removes_all_real_keys():
    keys = _user_server_keys(USER)
    for key in keys:
        operations.cache_set(key, ["server"])
    assert all(operations.cache_get(key) is not None for key in keys)

    assert invalidate_user_servers(USER) > 0

    assert all(operations.cache_get(key) is None for key in keys)


def test_invalidate_user_servers_is_scoped_to_the_user():
    mine = _user_server_keys(USER)
    others = _user_server_keys(OTHER_USER)
    for key in mine + others:
        operations.cache_set(key, ["server"])

    invalidate_user_servers(USER)

    assert all(operations.cache_get(key) is None for key in mine)
    # A user whose id contains the target id as a substring must survive.
    assert all(operations.cache_get(key) is not None for key in others)
