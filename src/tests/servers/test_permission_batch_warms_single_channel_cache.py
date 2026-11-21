"""Regression: get_permissions_batch must warm the key get_permissions reads.

Channel listing and search resolve per-channel ``messages.read`` with
``RoleHandler.get_permissions_batch``, which warms a permission cache for every
channel in one pass.  The single-channel ``get_permissions`` read that follows
during message hydration looks up the key ``perms:{user}:{server}:{channel}``.
The batch used to warm a ``(user, server, channel)`` tuple key instead, so the
warm entries were unreachable: each channel re-resolved the owner, member
roles and channel overrides from the database, and the warmed entries also
escaped the ``srv_permission:perms:{user}:{server}:*`` invalidation pattern.

These tests measure issued SQL from a cold query cache and pin both the reuse
and the invalidation-coverage properties.
"""

import uuid

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern
from src.core.database.cache.operations import cache_get
from src.core.servers.manager import member_role_rows_cache as mrc

pytestmark = [pytest.mark.unit, pytest.mark.servers]


def _canonical_key(user_id, server_id, channel_id):
    return f"srv_permission:perms:{int(user_id)}:{int(server_id)}:{int(channel_id)}"


def _cold(db):
    """Clear every permission cache tier but leave SQL state measurable."""
    mem_cache_clear_pattern("*")
    mrc.invalidate_member_role_rows_cache()
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()


def _sql_count(db, call):
    """Run ``call`` and count real SQL statements (query cache cleared)."""
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    result = call()
    return db.get_request_metrics()["query_count"], result


@pytest.fixture
def batch_channel(db, server_manager, test_server, user_pool):
    """A non-owner member (so the batch really computes) and one channel."""
    server, owner = test_server
    member = user_pool.get_user()
    server_manager.add_member(server.id, member.id)
    channel = server_manager.create_channel(
        owner.id, server.id, f"perm-batch-{uuid.uuid4().hex[:8]}"
    )
    return server_manager, member, server.id, channel


def test_batch_warm_is_reused_by_single_channel_resolution(db, batch_channel):
    """A batched resolve makes the next single-channel check SQL-free."""
    servers, member, server_id, channel = batch_channel
    role_handler = servers.role_handler

    _cold(db)
    batch = role_handler.get_permissions_batch(member.id, server_id, [channel.id])

    # The warm entry must live under the key the single-channel reader uses.
    assert (
        cache_get(_canonical_key(member.id, server_id, channel.id)) == batch[channel.id]
    )

    sql, single = _sql_count(
        db, lambda: role_handler.get_permissions(member.id, server_id, channel.id)
    )
    assert sql == 0, "batched permission result was not reused by the single read"
    assert single == batch[channel.id]


def test_batch_warmed_entry_is_covered_by_permission_invalidation(db, batch_channel):
    """The warmed entry must be evicted with the rest of the RBAC tiers."""
    servers, member, server_id, channel = batch_channel
    role_handler = servers.role_handler
    key = _canonical_key(member.id, server_id, channel.id)

    _cold(db)
    role_handler.get_permissions_batch(member.id, server_id, [channel.id])
    assert cache_get(key) is not None

    servers._invalidate_permission_caches(server_id, member.id)
    assert cache_get(key) is None, (
        "batched permission entry escaped permission-cache invalidation"
    )

    # A fresh resolve re-reads the authoritative tables after invalidation.
    sql, _ = _sql_count(
        db, lambda: role_handler.get_permissions(member.id, server_id, channel.id)
    )
    assert sql > 0
