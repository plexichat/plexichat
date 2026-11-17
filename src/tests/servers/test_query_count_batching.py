"""Query-count regressions for the hot server read paths.

Server core reads used to issue one override query per ``(channel, role)``
pair and one role-override query per role.  Those loops grew linearly with the
number of channels/roles, so a cold ``GET /servers/{id}/channels`` cost far
more DB round-trips than its small result set suggested.  These tests measure
query counts from a cold cache with small and large row counts and assert the
count stays *constant*.

They run the same read twice with very different row counts; a wall-clock
assert would miss a regression on a small local DB, but the query count grows
deterministically with an unbatched per-item call.
"""

import inspect
import uuid

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern
from src.core.servers.manager import server_ops as server_ops_module

pytestmark = pytest.mark.performance


def _queries(db, call):
    """Run ``call`` from a cold cache and return its statement count."""
    mem_cache_clear_pattern("*")
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    result = call()
    return db.get_request_metrics()["query_count"], result


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _add_channels(servers, owner_id, server_id, count):
    return [
        servers.create_channel(owner_id, server_id, _unique(f"chan{i}"))
        for i in range(count)
    ]


def _add_roles(servers, owner_id, server_id, count):
    """Give the owner extra roles so an override loop multiplies queries."""
    for i in range(count):
        role = servers.create_role(
            user_id=owner_id,
            server_id=server_id,
            name=_unique(f"role{i}"),
            permissions={"channels.view": True},
        )
        servers.assign_role(owner_id, server_id, owner_id, role.id)


def test_channel_listing_query_count_does_not_scale_with_channels(
    db, server_manager, test_server
):
    """Listing 20 channels must cost the same queries as listing two."""
    server, owner = test_server
    server_id = server.id
    _add_roles(server_manager, owner.id, server_id, 4)

    _add_channels(server_manager, owner.id, server_id, 2)
    one_queries, one = _queries(
        db, lambda: server_manager.get_channels(owner.id, server_id)
    )
    assert len(one) >= 3

    _add_channels(server_manager, owner.id, server_id, 18)
    many_queries, many = _queries(
        db, lambda: server_manager.get_channels(owner.id, server_id)
    )
    assert len(many) == len(one) + 18

    assert many_queries <= one_queries + 2, (
        "channel listing introduced an unbatched per-channel/per-role DB call: "
        f"{len(one)} channels -> {one_queries} queries, "
        f"{len(many)} channels -> {many_queries}"
    )


def test_channel_listing_query_count_does_not_scale_with_roles(
    db, server_manager, test_server
):
    """Adding roles must not add a query per (channel, role) pair."""
    server, owner = test_server
    _add_channels(server_manager, owner.id, server.id, 6)

    _add_roles(server_manager, owner.id, server.id, 2)
    one_queries, _ = _queries(
        db, lambda: server_manager.get_channels(owner.id, server.id)
    )

    _add_roles(server_manager, owner.id, server.id, 13)
    many_queries, _ = _queries(
        db, lambda: server_manager.get_channels(owner.id, server.id)
    )

    assert many_queries <= one_queries + 2, (
        "channel listing override lookup scales with the number of roles: "
        f"2 roles -> {one_queries} queries, 15 roles -> {many_queries}"
    )


def test_single_channel_permission_query_count_does_not_scale_with_roles(
    db, server_manager, test_server
):
    """Resolving one channel's permissions must not loop over roles in SQL."""
    server, owner = test_server
    channel = server_manager.create_channel(owner.id, server.id, _unique("perm"))
    _add_roles(server_manager, owner.id, server.id, 2)

    one_queries, _ = _queries(
        db,
        lambda: server_manager.get_permissions(owner.id, server.id, channel.id),
    )

    _add_roles(server_manager, owner.id, server.id, 13)
    many_queries, _ = _queries(
        db,
        lambda: server_manager.get_permissions(owner.id, server.id, channel.id),
    )

    assert many_queries <= one_queries + 2, (
        "single-channel permission resolution issues a per-role query: "
        f"2 roles -> {one_queries} queries, 15 roles -> {many_queries}"
    )


def test_server_list_counts_stay_correct_with_many_related_rows(
    db, server_manager, auth_manager, test_server
):
    """The fan-out-free server list keeps accurate member/channel/role counts."""
    server, owner = test_server
    server_id = server.id
    _add_channels(server_manager, owner.id, server_id, 12)
    _add_roles(server_manager, owner.id, server_id, 4)

    for i in range(4):
        tag = uuid.uuid4().hex[:10]
        member = auth_manager.register(
            username=f"qc_member_{tag}",
            email=f"qc_member_{tag}@example.test",
            password="QueryCount123!",
        )
        server_manager.add_member(server_id, member.id)

    _, listed = _queries(db, lambda: server_manager.get_servers(owner.id))
    row = next(s for s in listed if s.id == server_id)

    # 1 default channel + 12 added = 13; 1 default role + 4 added = 5;
    # owner + 4 added = 5 members.
    assert row.channel_count == 13
    assert row.role_count == 5
    assert row.member_count == 5


def test_server_list_uses_correlated_counts_not_a_cross_join():
    """``get_servers`` must not reintroduce the members x channels x roles join."""
    source = inspect.getsource(server_ops_module.ServerOpsMixin.get_servers)
    assert "COUNT(DISTINCT" not in source
    assert "GROUP BY" not in source
    assert "EXISTS" in source
    assert "COUNT(*)" in source
