"""
Tests for role operations.
"""

import pytest


class TestCreateRole:
    """Tests for role creation."""

    def test_create_role_success(self, fresh_server):
        """Test creating a role."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Moderator",
            permissions={"messages.manage": True},
        )

        assert role is not None
        assert role.name == "Moderator"
        assert role.permissions.get("messages.manage") is True

    def test_create_role_with_color(self, fresh_server):
        """Test creating role with color."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="VIP", color="#FFD700"
        )

        assert role.color == "#FFD700"

    def test_create_role_hoisted(self, fresh_server):
        """Test creating hoisted role."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Staff", hoist=True
        )

        assert role.hoist is True

    def test_create_role_mentionable(self, fresh_server):
        """Test creating mentionable role."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Helpers", mentionable=True
        )

        assert role.mentionable is True

    def test_create_role_empty_name_fails(self, fresh_server):
        """Test that empty name fails."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.InvalidRoleNameError):
            servers.create_role(user_id=owner.id, server_id=server.id, name="")

    def test_create_role_by_non_admin_fails(self, server_with_members):
        """Test that non-admin cannot create role."""
        server, _, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_role(
                user_id=member_user.id, server_id=server.id, name="Hacked Role"
            )


class TestGetRole:
    """Tests for getting role info."""

    def test_get_role_as_member(self, server_with_members):
        """Test getting role as member."""
        server, _, _, member_user, _, admin_role, servers = server_with_members

        role = servers.get_role(admin_role.id, member_user.id)

        assert role is not None
        assert role.id == admin_role.id

    def test_get_role_as_non_member(self, server_with_members):
        """Test getting role as non-member returns None."""
        server, _, _, _, outsider, admin_role, servers = server_with_members

        role = servers.get_role(admin_role.id, outsider.id)

        assert role is None


class TestGetRoles:
    """Tests for listing roles."""

    def test_get_roles_returns_server_roles(self, server_with_members):
        """Test getting all roles in server."""
        server, owner, _, _, _, admin_role, servers = server_with_members

        roles = servers.get_roles(owner.id, server.id)

        assert len(roles) >= 2  # @everyone + Admin
        role_ids = [r.id for r in roles]
        assert admin_role.id in role_ids

    def test_get_roles_includes_default(self, server_with_members):
        """Test that @everyone role is included."""
        server, owner, _, _, _, _, servers = server_with_members

        roles = servers.get_roles(owner.id, server.id)

        default_roles = [r for r in roles if r.is_default]
        assert len(default_roles) == 1

    def test_get_roles_ordered_by_position(self, server_with_members):
        """Test that roles are ordered by position (highest first)."""
        server, owner, _, _, _, _, servers = server_with_members

        roles = servers.get_roles(owner.id, server.id)

        positions = [r.position for r in roles]
        assert positions == sorted(positions, reverse=True)

    def test_get_roles_without_permission_fails(self, server_with_members):
        """Test that non-admins cannot enumerate server roles."""
        server, _, _, member_user, _, _, servers = server_with_members

        with pytest.raises(servers.PermissionDeniedError):
            servers.get_roles(member_user.id, server.id)


class TestUpdateRole:
    """Tests for updating roles."""

    def test_update_role_name(self, server_with_members):
        """Test updating role name."""
        server, owner, _, _, _, admin_role, servers = server_with_members

        updated = servers.update_role(
            user_id=owner.id, role_id=admin_role.id, name="Super Admin"
        )

        assert updated.name == "Super Admin"

    def test_update_role_permissions(self, server_with_members):
        """Test updating role permissions."""
        server, owner, _, _, _, admin_role, servers = server_with_members

        updated = servers.update_role(
            user_id=owner.id, role_id=admin_role.id, permissions={"administrator": True}
        )

        assert updated.permissions.get("administrator") is True

    def test_update_role_color(self, server_with_members):
        """Test updating role color."""
        server, owner, _, _, _, admin_role, servers = server_with_members

        updated = servers.update_role(
            user_id=owner.id, role_id=admin_role.id, color="#00FF00"
        )

        assert updated.color == "#00FF00"

    def test_update_default_role_name_fails(self, fresh_server):
        """Test that renaming @everyone fails."""
        server, owner, servers = fresh_server

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        with pytest.raises(servers.DefaultRoleError):
            servers.update_role(
                user_id=owner.id, role_id=default_role.id, name="Not Everyone"
            )

    def test_update_default_role_permissions_succeeds(self, fresh_server):
        """Test that updating @everyone permissions works."""
        server, owner, servers = fresh_server

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        updated = servers.update_role(
            user_id=owner.id,
            role_id=default_role.id,
            permissions={"messages.send": False},
        )

        assert updated.permissions.get("messages.send") is False


class TestDeleteRole:
    """Tests for deleting roles."""

    def test_delete_role(self, fresh_server):
        """Test deleting a role."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Temporary"
        )

        result = servers.delete_role(owner.id, role.id)

        assert result is True
        assert servers.get_role(role.id, owner.id) is None

    def test_delete_default_role_fails(self, fresh_server):
        """Test that deleting @everyone fails."""
        server, owner, servers = fresh_server

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        with pytest.raises(servers.DefaultRoleError):
            servers.delete_role(owner.id, default_role.id)

    def test_delete_role_removes_from_members(self, server_with_members):
        """Test that deleting role removes it from members."""
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        # Verify admin has the role
        member_roles = servers.get_member_roles(server.id, admin_user.id)
        assert any(r.id == admin_role.id for r in member_roles)

        # Delete the role
        servers.delete_role(owner.id, admin_role.id)

        # Verify role is removed from member
        member_roles = servers.get_member_roles(server.id, admin_user.id)
        assert not any(r.id == admin_role.id for r in member_roles)


class TestRoleHierarchy:
    """Tests for role hierarchy enforcement."""

    def test_cannot_modify_higher_role(self, server_with_members):
        """Test that user cannot modify role above their highest."""
        server, owner, admin_user, _, _, admin_role, servers = server_with_members

        # Give admin roles.manage permission
        servers.update_role(
            user_id=owner.id,
            role_id=admin_role.id,
            permissions={**admin_role.permissions, "roles.manage": True},
        )

        # Create a role above admin
        higher_role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Higher Role"
        )
        servers.move_role(owner.id, higher_role.id, position=admin_role.position + 1)

        # Admin should not be able to modify higher role
        with pytest.raises(servers.RoleHierarchyError):
            servers.update_role(
                user_id=admin_user.id, role_id=higher_role.id, name="Hacked"
            )

    def test_owner_can_modify_any_role(self, server_with_members):
        """Test that owner can modify any role."""
        server, owner, _, _, _, admin_role, servers = server_with_members

        # Create highest role
        highest = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Highest"
        )
        servers.move_role(owner.id, highest.id, position=100)

        # Owner can still modify it
        updated = servers.update_role(
            user_id=owner.id, role_id=highest.id, name="Still Highest"
        )

        assert updated.name == "Still Highest"


class TestMoveRole:
    """Tests for moving roles."""

    def test_move_role(self, fresh_server):
        """Test moving a role."""
        server, owner, servers = fresh_server

        role = servers.create_role(
            user_id=owner.id, server_id=server.id, name="Movable"
        )

        updated = servers.move_role(owner.id, role.id, position=5)

        assert updated.position == 5

    def test_move_default_role_fails(self, fresh_server):
        """Test that moving @everyone fails."""
        server, owner, servers = fresh_server

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        with pytest.raises(servers.DefaultRoleError):
            servers.move_role(owner.id, default_role.id, position=5)


class TestPermissionGrantSecurity:
    """Editors may only grant permissions they possess; wildcards are rejected."""

    def _make_moderator(self, servers, server, owner):
        """Create a moderator (roles.manage only) below the admin-tier roles."""
        import uuid

        auth = servers._auth
        unique_id = uuid.uuid4().hex[:8]
        mod_user = auth.register(
            f"grant_mod_{unique_id}",
            f"grant_mod_{unique_id}@example.com",
            "TestPass123!",
        )
        servers.add_member(server.id, mod_user.id)

        mod_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Grant Mod",
            permissions={"roles.manage": True, "members.manage_roles": True},
        )
        # Park the moderator role high but below the owner's reach check
        servers.move_role(owner.id, mod_role.id, position=5)
        servers.assign_role(owner.id, server.id, mod_user.id, mod_role.id)
        return mod_user, mod_role

    def test_create_wildcard_permission_rejected(self, fresh_server):
        """Test that "*" cannot be stored on a role."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.InvalidPermissionKeyError):
            servers.create_role(
                user_id=owner.id,
                server_id=server.id,
                name="Wildcard",
                permissions={"*": True},
            )

    def test_create_namespace_wildcard_rejected(self, fresh_server):
        """Test that "namespace.*" keys cannot be stored on a role."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.InvalidPermissionKeyError):
            servers.create_role(
                user_id=owner.id,
                server_id=server.id,
                name="NsWildcard",
                permissions={"channels.*": True},
            )

    def test_create_unknown_permission_key_rejected(self, fresh_server):
        """Test that arbitrary keys are validated against SERVER_PERMISSIONS."""
        server, owner, servers = fresh_server

        with pytest.raises(servers.InvalidPermissionKeyError):
            servers.create_role(
                user_id=owner.id,
                server_id=server.id,
                name="Bogus",
                permissions={"not.a.real.permission": True},
            )

    def test_moderator_cannot_grant_permissions_it_lacks(self, fresh_server):
        """Test that a roles.manage holder cannot create an administrator role."""
        server, owner, servers = fresh_server
        mod_user, _ = self._make_moderator(servers, server, owner)

        with pytest.raises(servers.PermissionDeniedError):
            servers.create_role(
                user_id=mod_user.id,
                server_id=server.id,
                name="Escalated",
                permissions={"administrator": True},
            )

    def test_moderator_cannot_update_role_with_perms_it_lacks(self, fresh_server):
        """Test that editing a lower role cannot inject unheld permissions."""
        server, owner, servers = fresh_server
        mod_user, mod_role = self._make_moderator(servers, server, owner)

        helper = servers.create_role(
            user_id=mod_user.id, server_id=server.id, name="Helper"
        )

        with pytest.raises(servers.PermissionDeniedError):
            servers.update_role(
                user_id=mod_user.id,
                role_id=helper.id,
                permissions={"members.ban": True},
            )

    def test_moderator_can_grant_permissions_it_holds(self, fresh_server):
        """Test granting held permissions still works."""
        import uuid

        server, owner, servers = fresh_server
        auth = servers._auth
        unique_id = uuid.uuid4().hex[:8]
        lead = auth.register(
            f"greeter_lead_{unique_id}",
            f"greeter_lead_{unique_id}@example.com",
            "TestPass123!",
        )
        servers.add_member(server.id, lead.id)

        # Lead sits above the Welcomer role and holds every permission it may
        # hand out, including the one being added.
        lead_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Greeter Lead",
            permissions={
                "roles.manage": True,
                "messages.send": True,
                "messages.read": True,
            },
        )
        servers.move_role(owner.id, lead_role.id, position=5)
        servers.assign_role(owner.id, server.id, lead.id, lead_role.id)

        welcomer = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Welcomer",
            permissions={"messages.send": True},
        )
        servers.move_role(owner.id, welcomer.id, position=2)

        updated = servers.update_role(
            user_id=lead.id,
            role_id=welcomer.id,
            permissions={"messages.send": True, "messages.read": True},
        )
        assert updated.permissions.get("messages.read") is True

    def test_assign_role_with_unheld_permissions_fails(self, fresh_server):
        """Test that attaching a role grants nothing the editor lacks."""
        import uuid

        server, owner, servers = fresh_server
        mod_user, mod_role = self._make_moderator(servers, server, owner)

        target = servers._auth.register(
            f"assign_target_{uuid.uuid4().hex[:8]}",
            f"assign_target_{uuid.uuid4().hex[:8]}@example.com",
            "TestPass123!",
        )
        servers.add_member(server.id, target.id)

        trusted = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Trusted",
            permissions={"members.kick": True},
        )
        servers.move_role(owner.id, trusted.id, position=2)

        with pytest.raises(servers.PermissionDeniedError):
            servers.assign_role(mod_user.id, server.id, target.id, trusted.id)

    def test_created_role_position_below_creators_highest(self, fresh_server):
        """Test hierarchy on create: new role lands below creator's highest."""
        server, owner, servers = fresh_server
        mod_user, _ = self._make_moderator(servers, server, owner)

        created = servers.create_role(
            user_id=mod_user.id, server_id=server.id, name="Mod Created"
        )
        assert created.position < 5


class TestUpdateRolePosition:
    """PATCH-style position updates via update_role."""

    def test_update_role_position(self, fresh_server):
        """Test moving a role via update_role."""
        server, owner, servers = fresh_server

        role = servers.create_role(user_id=owner.id, server_id=server.id, name="Mover")
        updated = servers.update_role(user_id=owner.id, role_id=role.id, position=3)
        assert updated.position == 3

    def test_update_role_position_above_own_highest_fails(self, fresh_server):
        """Test a non-owner cannot move a role above their highest role."""
        server, owner, servers = fresh_server
        mod_user, mod_role = self._make_moderator_positioned(servers, server, owner)

        other = servers.create_role(
            user_id=mod_user.id, server_id=server.id, name="Peon"
        )

        with pytest.raises(servers.RoleHierarchyError):
            servers.update_role(user_id=mod_user.id, role_id=other.id, position=10)

    def test_update_default_role_position_fails(self, fresh_server):
        """Test the default role cannot be moved via update_role."""
        server, owner, servers = fresh_server

        roles = servers.get_roles(owner.id, server.id)
        default_role = [r for r in roles if r.is_default][0]

        with pytest.raises(servers.DefaultRoleError):
            servers.update_role(user_id=owner.id, role_id=default_role.id, position=9)

    @staticmethod
    def _make_moderator_positioned(servers, server, owner):
        import uuid

        auth = servers._auth
        unique_id = uuid.uuid4().hex[:8]
        mod_user = auth.register(
            f"pos_mod_{unique_id}",
            f"pos_mod_{unique_id}@example.com",
            "TestPass123!",
        )
        servers.add_member(server.id, mod_user.id)

        mod_role = servers.create_role(
            user_id=owner.id,
            server_id=server.id,
            name="Position Mod",
            permissions={"roles.manage": True},
        )
        servers.move_role(owner.id, mod_role.id, position=5)
        servers.assign_role(owner.id, server.id, mod_user.id, mod_role.id)
        return mod_user, mod_role
