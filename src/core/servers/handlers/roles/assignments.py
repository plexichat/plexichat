"""Member-role assignments, including the self-service whitelist path."""

import json
from typing import List

from src.core.base import SnowflakeID

from ...models import Role, AuditLogAction
from ...manager.converters import _row_to_role
from ...exceptions import (
    RoleNotFoundError,
    RoleHierarchyError,
    DefaultRoleError,
    MemberNotFoundError,
)
from ...permission_utils import (
    can_manage_role,
    validate_permission_dict,
    check_permissions_grantable,
)
from .protocol import RoleHandlerProtocol


class RoleAssignmentMixin(RoleHandlerProtocol):
    def get_member_roles(
        self, server_id: SnowflakeID, member_user_id: SnowflakeID
    ) -> List[Role]:
        """Get all roles assigned to a member."""
        member = self.manager.get_member(server_id, member_user_id)
        if not member:
            return []

        rows = self.db.fetch_all(
            """SELECT r.* FROM srv_roles r
               INNER JOIN srv_member_roles mr ON r.id = mr.role_id
               WHERE mr.member_id = ? AND r.deleted = 0
               ORDER BY r.position DESC""",
            (member.id,),
        )
        return [_row_to_role(row) for row in rows]

    def assign_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Assign a role to a member."""
        self.manager.require_permission(user_id, server_id, "members.manage_roles")
        role = self.get_role(role_id, user_id)
        if not role or role.server_id != server_id:
            raise RoleNotFoundError("Role not found")

        member = self.manager.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        user_roles = self.manager._get_member_role_rows(server_id, user_id)
        server = self.manager.get_server(server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot assign a role at or above your highest role"
            )

        # Attaching a role grants its permissions to the target, so the editor
        # must possess every permission the role carries (incl. administrator).
        role_permissions = role.permissions or {}
        validate_permission_dict(role_permissions)
        editor_perms = self.get_permissions(user_id, server_id)
        check_permissions_grantable(editor_perms, role_permissions)

        existing = self.db.fetch_one(
            "SELECT 1 FROM srv_member_roles WHERE member_id = ? AND role_id = ?",
            (member.id, role_id),
        )
        if existing:
            return True

        now = self.manager._get_timestamp()
        self.db.execute(
            """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at, assigned_by)
               VALUES (?, ?, ?, ?, ?)""",
            (self.manager._generate_id(), member.id, role_id, now, user_id),
        )

        # Invalidate caches
        from src.core.database import invalidate_pattern

        self.manager._cache_invalidate(self.manager._permission_cache_prefix)
        invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")
        invalidate_pattern("has_perm:*")
        invalidate_pattern(f"member_data:*{member_user_id}*")

        self.manager._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_ROLE_ADD,
            "member",
            member_user_id,
            {"role_id": role_id},
        )
        return True

    def remove_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Remove a role from a member."""
        self.manager.require_permission(user_id, server_id, "members.manage_roles")
        role = self.get_role(role_id, user_id)
        if not role or role.server_id != server_id:
            raise RoleNotFoundError("Role not found")

        if role.is_default:
            raise DefaultRoleError("Cannot remove the default role")

        member = self.manager.get_member(server_id, member_user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        user_roles = self.manager._get_member_role_rows(server_id, user_id)
        server = self.manager.get_server(server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot remove a role at or above your highest role"
            )

        self.db.execute(
            "DELETE FROM srv_member_roles WHERE member_id = ? AND role_id = ?",
            (member.id, role_id),
        )

        # Invalidate caches
        from src.core.database import invalidate_pattern

        self.manager._cache_invalidate(self.manager._permission_cache_prefix)
        invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")
        invalidate_pattern("has_perm:*")
        invalidate_pattern(f"member_data:*{member_user_id}*")

        self.manager._log_audit(
            server_id,
            user_id,
            AuditLogAction.MEMBER_ROLE_REMOVE,
            "member",
            member_user_id,
            {"role_id": role_id},
        )
        return True

    def assign_roles_from_whitelist(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        role_ids: List[SnowflakeID],
        allowed_role_ids: List[SnowflakeID],
    ) -> List[SnowflakeID]:
        """Self-service role assignment for onboarding steps.

        Assigns the given roles to ``user_id``, but only those present in
        ``allowed_role_ids`` - the admin-defined whitelist captured in the
        onboarding step's options. Refuses the default role and any role that
        grants the ``administrator`` permission to prevent privilege
        escalation through self-service flows.

        Returns the list of role IDs actually assigned.
        """
        member = self.manager.get_member(server_id, user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        allowed = set(allowed_role_ids or [])
        now = self.manager._get_timestamp()
        assigned: List[SnowflakeID] = []

        for role_id in role_ids:
            if role_id not in allowed:
                continue
            row = self.db.fetch_one(
                "SELECT * FROM srv_roles WHERE id = ? AND deleted = 0",
                (role_id,),
            )
            if not row or row["server_id"] != server_id:
                continue
            if row["is_default"]:
                continue

            permissions = row["permissions"]
            if isinstance(permissions, str):
                permissions = json.loads(permissions) if permissions else {}
            if permissions.get("administrator"):
                continue

            existing = self.db.fetch_one(
                "SELECT 1 FROM srv_member_roles WHERE member_id = ? AND role_id = ?",
                (member.id, role_id),
            )
            if existing:
                assigned.append(role_id)
                continue

            self.db.execute(
                """INSERT INTO srv_member_roles (id, member_id, role_id, assigned_at, assigned_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (self.manager._generate_id(), member.id, role_id, now, user_id),
            )
            self.manager._log_audit(
                server_id,
                user_id,
                AuditLogAction.MEMBER_ROLE_ADD,
                "member",
                user_id,
                {"role_id": role_id},
            )
            assigned.append(role_id)

        if assigned:
            from src.core.database import invalidate_pattern

            self.manager._cache_invalidate(self.manager._permission_cache_prefix)
            invalidate_pattern(f"perms:{user_id}:{server_id}:*")
            invalidate_pattern("has_perm:*")
            invalidate_pattern(f"member_data:*{user_id}*")

        return assigned
