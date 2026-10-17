"""
Regression tests for server permission/membership cache invalidation.

Covers the bugs where RBAC mutations (role permission changes, role deletion,
ownership transfer, kick/ban, member timeout) were not clearing every cache
tier, so revoked permissions kept being served for up to the cache TTL.
"""

import time


class TestCacheGetFalsyValues:
    """``_cache_get`` must treat falsy cached values as hits, not misses."""

    def test_false_value_is_returned(self, modules):
        servers = modules.servers

        servers._cache_set("srv_member:", "is_member:999999:888888", False)
        assert servers._cache_get("srv_member:", "is_member:999999:888888") is False

    def test_empty_dict_value_is_returned(self, modules):
        servers = modules.servers

        servers._cache_set("srv_permission:", "perms:999999:888888:0", {})
        assert servers._cache_get("srv_permission:", "perms:999999:888888:0") == {}

    def test_zero_value_is_returned(self, modules):
        servers = modules.servers

        servers._cache_set("srv_owner:", "0", 0)
        assert servers._cache_get("srv_owner:", "0") == 0


class TestRolePermissionChangeInvalidation:
    """Role permission changes must invalidate cached permission results."""

    def test_update_role_revocation_takes_effect_immediately(self, server_with_members):
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        # Warm the cache: admin currently holds members.ban via the admin role.
        assert servers.has_permission(admin_user.id, server.id, "members.ban") is True

        servers.update_role(
            user_id=owner.id,
            role_id=admin_role.id,
            permissions={**admin_role.permissions, "members.ban": False},
        )

        # The revocation must be visible now, not after the cache TTL.
        assert servers.has_permission(admin_user.id, server.id, "members.ban") is False

    def test_delete_role_revocation_takes_effect_immediately(self, server_with_members):
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        # Warm the cache.
        assert servers.has_permission(admin_user.id, server.id, "members.kick") is True

        servers.delete_role(owner.id, admin_role.id)

        assert servers.has_permission(admin_user.id, server.id, "members.kick") is False


class TestOwnershipTransferInvalidation:
    """Ownership transfer must revoke the old owner's cached admin access."""

    def test_old_owner_loses_admin_immediately(self, server_with_members):
        server, owner, admin_user, _, _, _, servers = server_with_members

        # Warm the old owner's permission snapshot (is_owner=True -> all perms).
        assert servers.has_permission(owner.id, server.id, "administrator") is True

        servers.transfer_ownership(owner.id, server.id, admin_user.id)

        # The old owner is now a regular member and must lose admin rights now.
        assert servers.has_permission(owner.id, server.id, "administrator") is False
        # The new owner gains them immediately.
        assert servers.has_permission(admin_user.id, server.id, "administrator") is True


class TestMembershipChangeInvalidation:
    """Kick/ban/timeout must invalidate cached membership and permissions."""

    def test_kick_member_revokes_cached_permissions(self, server_with_members):
        server, owner, _, member_user, _, _, servers = server_with_members

        assert (
            servers.has_permission(member_user.id, server.id, "messages.send") is True
        )

        servers.kick_member(owner.id, server.id, member_user.id)

        assert (
            servers.has_permission(member_user.id, server.id, "messages.send") is False
        )
        assert servers.get_member(server.id, member_user.id) is None

    def test_timeout_revokes_cached_permissions(self, server_with_members):
        server, owner, _, member_user, _, _, servers = server_with_members

        assert (
            servers.has_permission(member_user.id, server.id, "messages.send") is True
        )

        servers.update_member(
            owner.id,
            server.id,
            member_user.id,
            timeout_until=int(time.time() * 1000) + 60_000,
        )

        # A timed-out member must lose message-send immediately.
        assert (
            servers.has_permission(member_user.id, server.id, "messages.send") is False
        )
