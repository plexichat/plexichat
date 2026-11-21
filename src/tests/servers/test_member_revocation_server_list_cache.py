"""Core membership revocation must evict the per-viewer server-list cache.

``ServerManager.get_servers`` memoizes each viewer's joined-server list under
``user_servers:int:<uid>`` (default limit) and ``user_servers:int:<uid>:...``
(explicit limit) via ``@cached(ttl=15, prefix="user_servers")``.  That list is
a per-viewer data set derived entirely from ``srv_members``.  Every core
revocation path (kick, ban, leave, temporary-member reaping, and the
automod/admin/bot callers that never reach the HTTP helper) funnels through
``CacheOpsMixin._invalidate_member_caches``.  That funnel only deleted the
vestigial plain key ``user_servers:<uid>`` -- a key the decorator never writes
-- so a removed member kept reading a still-warm 200 server list (still
containing the server they were just removed from) for the full 15s TTL.

The HTTP-only leave/transfer paths happen to call ``invalidate_user_servers``,
so this gap is specific to the shared core funnel and its kick/ban/reap
callers.  Regression is pinned at the funnel so every such caller is covered.
"""

from types import SimpleNamespace

import pytest

from src.core.database.cache.serialization import generate_cache_key

pytestmark = [pytest.mark.unit, pytest.mark.security]

USER_ID = 42
SERVER_ID = 10


def _run_funnel(monkeypatch):
    """Invoke the shared core revocation funnel with cache calls recorded."""
    import src.core.database as dbmod
    import src.core.servers.manager.cache_ops as cache_ops
    import src.core.servers.manager.member_count_cache as member_count_cache

    deleted: list = []
    patterns: list = []
    monkeypatch.setattr(dbmod, "cache_delete", lambda key: deleted.append(key))
    monkeypatch.setattr(dbmod, "invalidate_pattern", lambda pat: patterns.append(pat))
    monkeypatch.setattr(
        member_count_cache, "invalidate_member_count_cache", lambda: None
    )

    # The mixin only needs these two collaborator hooks for this funnel; stub
    # them so the test exercises the cache eviction lines in isolation.
    stub = SimpleNamespace(
        _roster_cache_prefix="srv_roster",
        _invalidate_roster_caches=lambda server_id: None,
        _invalidate_permission_caches=lambda server_id, user_id: None,
    )
    cache_ops.CacheOpsMixin._invalidate_member_caches(stub, SERVER_ID, USER_ID)
    return deleted, patterns


def test_revocation_drops_real_get_servers_cache_keys(monkeypatch):
    """The exact keys the @cached get_servers tier writes must be evicted."""
    deleted, patterns = _run_funnel(monkeypatch)

    # Keys produced by the bound ``get_servers`` method. The wrapper receives
    # ``(self, user_id, [limit])`` and drops ``self`` before building the key.
    default_key = generate_cache_key("user_servers", USER_ID)
    paged_key = generate_cache_key("user_servers", USER_ID, 100)
    assert default_key == "user_servers:int:42"
    assert paged_key.startswith("user_servers:int:42:")

    # Default-limit entry (the common GET /servers call) is a direct delete;
    # explicit-limit entries are covered by a scoped pattern. The old code
    # deleted only ``user_servers:42``, which matches neither real key.
    assert default_key in deleted, "default-limit server-list cache not evicted"
    assert f"user_servers:int:{USER_ID}:*" in patterns, (
        "explicit-limit server-list cache namespace not evicted"
    )


def test_revocation_keeps_plan_plain_key_delete(monkeypatch):
    """The pre-existing plain-key delete is retained for backwards safety."""
    deleted, _patterns = _run_funnel(monkeypatch)
    assert f"user_servers:{USER_ID}" in deleted
