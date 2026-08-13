"""
Regression tests for audited server-system bug fixes.

These cover the bugs found during the servers-system audit:
1. Duplicated timeout block in get_permissions.
2. update_member timeout requires members.manage_roles (system actor exempt).
3. Redundant mute/deaf fields removed from Member model/to_dict.
4. get_members pagination ordering consistency.
5. unban_member permission enforcement.
6. Channel overrides gated on channels.manage (not roles.manage).
7. get_bans no longer requires the undefined bans.view permission.
"""

import time
import uuid

import pytest


class TestGetPermissionsTimeout:
    """Fix #1: duplicated timeout block in get_permissions."""

    def test_timed_out_member_loses_interaction_perms(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )

        # Owner times out the plain member (owner bypasses perm checks).
        servers.update_member(
            owner.id,
            server.id,
            member_user.id,
            timeout_until=int(time.time() * 1000) + 60_000,
            timeout_reason="test",
        )

        perms = servers.get_permissions(member_user.id, server.id)
        assert perms.get("messages.send") is False
        assert perms.get("voice.speak") is False
        assert perms.get("messages.read") is True  # unrelated perm unchanged

    def test_expired_timeout_restores_perms(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )

        servers.update_member(
            owner.id,
            server.id,
            member_user.id,
            timeout_until=int(time.time() * 1000) - 1000,
        )

        perms = servers.get_permissions(member_user.id, server.id)
        assert perms.get("messages.send") is True


class TestUpdateMemberTimeoutPermission:
    """Fix #2: timeout requires members.manage_roles for real users."""

    def test_plain_member_cannot_timeout_others(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        # member_user has no moderation perms and is not the system actor.
        with pytest.raises(servers.PermissionDeniedError):
            servers.update_member(
                member_user.id,
                server.id,
                member_user.id,
                timeout_until=int(time.time() * 1000) + 60_000,
            )

    def test_admin_can_timeout_others(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        # admin_user has members.manage_roles.
        servers.update_member(
            admin_user.id,
            server.id,
            member_user.id,
            timeout_until=int(time.time() * 1000) + 60_000,
        )
        member = servers.get_member(server.id, member_user.id)
        assert member.timeout_until is not None

    def test_system_actor_can_timeout_without_perms(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        # user_id == 0 simulates the AutoMod system actor.
        servers.update_member(
            0,
            server.id,
            member_user.id,
            timeout_until=int(time.time() * 1000) + 60_000,
            timeout_reason="Automod",
        )
        member = servers.get_member(server.id, member_user.id)
        assert member.timeout_until is not None


class TestMemberModelFields:
    """Fix #3: redundant mute/deaf fields removed."""

    def test_member_to_dict_uses_muted_deafened(self):
        from src.core.servers.models import Member

        member = Member(id=1, server_id=2, user_id=3, joined_at=0, updated_at=0)
        data = member.to_dict()
        assert "muted" in data
        assert "deafened" in data
        assert "mute" not in data
        assert "deaf" not in data


class TestGetMembersPagination:
    """Fix #4: get_members pagination ordering consistency."""

    def test_members_ordered_by_id(self, fresh_server, base_users):
        server, owner, servers = fresh_server
        u1, u2, u3, u4, _, _, _ = base_users

        servers.add_member(server.id, u2.id)
        servers.add_member(server.id, u3.id)
        servers.add_member(server.id, u4.id)

        members = servers.get_members(owner.id, server.id, limit=50)
        ids = [int(m.id) for m in members]
        assert ids == sorted(ids)

    def test_after_id_pagination_is_consistent(self, fresh_server, base_users):
        server, owner, servers = fresh_server
        u1, u2, u3, u4, _, _, _ = base_users

        servers.add_member(server.id, u1.id)
        servers.add_member(server.id, u2.id)
        servers.add_member(server.id, u3.id)

        first = servers.get_members(owner.id, server.id, limit=2)
        assert len(first) == 2
        last_id = max(int(m.id) for m in first)

        rest = servers.get_members(owner.id, server.id, limit=50, after_id=last_id)
        # Nothing before last_id should reappear.
        assert all(int(m.id) > last_id for m in rest)


class TestUnbanPermissions:
    """Fix #5: unban_member permission enforcement."""

    def test_non_mod_cannot_unban(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        servers.ban_member(owner.id, server.id, outsider.id, reason="x")

        # Plain member_user has no ban permission.
        with pytest.raises(servers.PermissionDeniedError):
            servers.unban_member(member_user.id, server.id, outsider.id)

    def test_mod_with_ban_perm_can_unban(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        servers.ban_member(owner.id, server.id, outsider.id, reason="x")
        # admin_user has members.ban.
        servers.unban_member(admin_user.id, server.id, outsider.id)
        bans = servers.get_bans(admin_user.id, server.id)
        assert all(b.user_id != outsider.id for b in bans)


class TestChannelOverridePermission:
    """Fix #6: channel overrides require channels.manage, not roles.manage."""

    def _make_channel(self, server, owner, servers):
        unique = uuid.uuid4().hex[:6]
        return servers.create_channel(owner.id, server.id, f"chan-{unique}")

    def test_roles_manage_only_cannot_set_override(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        channel = self._make_channel(server, owner, servers)

        # Role granting ONLY roles.manage (no channels.manage).
        role = servers.create_role(
            owner.id,
            server.id,
            name="roleman",
            permissions={"roles.manage": True},
            hoist=False,
        )
        servers.assign_role(owner.id, server.id, member_user.id, role.id)

        with pytest.raises(servers.PermissionDeniedError):
            servers.set_channel_override(
                member_user.id,
                channel.id,
                "member",
                outsider.id,
                allow={"messages.send": True},
            )

    def test_channels_manage_can_set_override(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        channel = self._make_channel(server, owner, servers)

        # admin_user has channels.manage.
        override = servers.set_channel_override(
            admin_user.id,
            channel.id,
            "member",
            outsider.id,
            allow={"messages.send": True},
        )
        assert override is not None


class TestGetBansPermission:
    """Fix #7: get_bans works for members.ban holders (not undefined bans.view)."""

    def test_mod_with_ban_perm_can_view_bans(self, server_with_members):
        server, owner, admin_user, member_user, outsider, _, servers = (
            server_with_members
        )
        servers.ban_member(owner.id, server.id, outsider.id, reason="x")

        # admin_user has members.ban but is NOT an administrator; this used to
        # raise PermissionDeniedError because it required the undefined
        # bans.view permission.
        bans = servers.get_bans(admin_user.id, server.id)
        assert any(b.user_id == outsider.id for b in bans)


def _new_user(base_users):
    """Helper to draw a fresh user from the base_users fixture."""
    u1, u2, u3, u4, _, _, _ = base_users
    return u1
