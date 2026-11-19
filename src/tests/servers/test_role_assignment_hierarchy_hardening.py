"""Regressions: role assignment/removal must respect the *member* hierarchy.

``assign_role`` and ``remove_role`` used to check only the position of the role
being edited (``can_manage_role``). They never checked the rank of the member
whose roles were being changed, nor the "owner is untouchable" rule that guards
kick/ban/update/channel-override/voice-moderation. A moderator holding
``members.manage_roles`` could therefore strip a role from an outranking member
(or the owner) as long as the role itself sat below the moderator's own role.
"""

import uuid

import pytest


def _new_member(servers, server, prefix):
    auth = servers._auth
    tag = uuid.uuid4().hex[:8]
    user = auth.register(
        f"{prefix}_{tag}", f"{prefix}_{tag}@example.com", "TestPass123!"
    )
    servers.add_member(server.id, user.id)
    return user


class TestRoleAssignmentMemberHierarchy:
    def _setup_moderator_and_target(self, fresh_server):
        server, owner, servers = fresh_server

        mod_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Role Moderator",
            permissions={"members.manage_roles": True, "roles.manage": True},
        )
        servers.move_role(owner.id, mod_role.id, position=5)

        low_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Low Role",
            permissions={"messages.send": True},
        )
        servers.move_role(owner.id, low_role.id, position=2)

        high_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="High Role",
            permissions={"messages.send": True},
        )
        servers.move_role(owner.id, high_role.id, position=9)

        moderator = _new_member(servers, server, "role_mod")
        target = _new_member(servers, server, "role_target")
        servers.assign_role(owner.id, server.id, moderator.id, mod_role.id)
        # The target outranks the moderator (9 > 5) but also holds the low role.
        servers.assign_role(owner.id, server.id, target.id, high_role.id)
        servers.assign_role(owner.id, server.id, target.id, low_role.id)

        return server, owner, moderator, target, low_role, servers

    def test_remove_role_rejects_outranked_member(self, fresh_server):
        server, owner, moderator, target, low_role, servers = (
            self._setup_moderator_and_target(fresh_server)
        )

        # The low role (position 2) is below the moderator's role (5), so the
        # old can_manage_role check passed and the strip succeeded. The target
        # member (position 9) outranks the moderator and must be protected.
        with pytest.raises(servers.RoleHierarchyError):
            servers.remove_role(moderator.id, server.id, target.id, low_role.id)

        role_ids = [r.id for r in servers.get_member_roles(server.id, target.id)]
        assert low_role.id in role_ids

    def test_assign_role_rejects_outranked_member(self, fresh_server):
        server, owner, moderator, target, low_role, servers = (
            self._setup_moderator_and_target(fresh_server)
        )
        # Drop the low role from the target first so assign is a real change.
        servers.remove_role(owner.id, server.id, target.id, low_role.id)

        with pytest.raises(servers.RoleHierarchyError):
            servers.assign_role(moderator.id, server.id, target.id, low_role.id)

        role_ids = [r.id for r in servers.get_member_roles(server.id, target.id)]
        assert low_role.id not in role_ids

    def test_member_target_operations_reject_the_owner(self, fresh_server):
        server, owner, moderator, target, low_role, servers = (
            self._setup_moderator_and_target(fresh_server)
        )
        # The owner is never a valid target of another member's role edits,
        # even for a low-position role the moderator technically outranks.
        servers.assign_role(owner.id, server.id, owner.id, low_role.id)
        with pytest.raises(servers.RoleHierarchyError):
            servers.remove_role(moderator.id, server.id, owner.id, low_role.id)

    def test_owner_and_lower_ranked_targets_still_work(self, fresh_server):
        server, owner, moderator, target, low_role, servers = (
            self._setup_moderator_and_target(fresh_server)
        )
        # No regression: the owner can still edit any member, and a moderator
        # can still manage the roles of a member they outrank.
        servers.remove_role(owner.id, server.id, target.id, low_role.id)
        assert low_role.id not in [
            r.id for r in servers.get_member_roles(server.id, target.id)
        ]

        junior = _new_member(servers, server, "role_junior")
        servers.assign_role(moderator.id, server.id, junior.id, low_role.id)
        assert low_role.id in [
            r.id for r in servers.get_member_roles(server.id, junior.id)
        ]
