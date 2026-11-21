"""Member-role assignments, including the self-service whitelist path."""

import json
from typing import List

from src.core.base import SnowflakeID

from ...channel_access_cache import get_channel_access_cache
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
    can_manage_member,
    has_permission,
    validate_permission_dict,
    check_permissions_grantable,
)
from .protocol import RoleHandlerProtocol


class RoleAssignmentMixin(RoleHandlerProtocol):
    def _guard_member_target(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
    ) -> None:
        """Refuse role edits on members the actor cannot manage.

        ``assign_role`` / ``remove_role`` historically checked only the
        position of the *role* being changed (``can_manage_role``) and never
        the rank of the *member* whose roles were being edited. That let a
        holder of ``members.manage_roles`` strip or attach roles on a member
        outranking them -- and on the server owner -- whenever the role itself
        sat below the actor's highest role. Every other member-target operation
        (kick/ban/update, channel overrides, voice moderation) already applies
        ``can_manage_member``; this mirrors that rule. Self-service changes are
        left allowed.
        """
        # Self-service role changes (adding/removing a role on yourself) are
        # legitimate; the hierarchy rule only governs changes to other members.
        if int(user_id) == int(member_user_id):
            return

        server = self.manager.get_server(server_id, user_id)
        owner_id = server.owner_id if server is not None else None
        # SECURITY: both ids here are DB-sourced -- owner_id is projected from
        # the srv_servers row, member_user_id from the srv_members row resolved
        # by the API route. Loose ``==`` let a text/float encoded owner id
        # ("5", 5.9) compare unequal to the genuine int 5, so target_is_owner
        # was False and a moderator could edit the server owner's roles.
        # Fail closed: a genuine int attests to identity (type() also rejects
        # bool). A malformed owner row or target id cannot prove the target is
        # NOT the owner, so deny the edit rather than granting the hierarchy
        # bypass.
        if server is not None and type(owner_id) is not int:
            raise RoleHierarchyError("Cannot manage roles: malformed server owner row")
        if type(member_user_id) is not int:
            raise RoleHierarchyError("Cannot manage roles: malformed member id row")
        is_owner = server is not None and owner_id == int(user_id)
        target_is_owner = server is not None and owner_id == member_user_id
        if target_is_owner:
            raise RoleHierarchyError("Cannot manage the roles of the server owner")

        user_roles = self.manager._get_member_role_rows(server_id, user_id)
        target_roles = self.manager._get_member_role_rows(server_id, member_user_id)
        if not can_manage_member(user_roles, target_roles, is_owner, target_is_owner):
            raise RoleHierarchyError(
                "Cannot manage the roles of a member at or above your own rank"
            )

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

        # The role-position check below is not enough on its own: changing a
        # member's roles is a member-target action and must respect that
        # member's rank (and never target the owner).
        self._guard_member_target(user_id, server_id, member_user_id)

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
        # Retire the target member's cached channel-view decisions; a newly
        # assigned role can immediately widen (and later revoke) channel access.
        get_channel_access_cache().bump(server_id, member_user_id)
        # The cached roster page embeds role ids, so a role change must evict
        # it for every viewer of this server.
        self.manager._invalidate_roster_caches(server_id)

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

        # The role-position check below is not enough on its own: changing a
        # member's roles is a member-target action and must respect that
        # member's rank (and never target the owner).
        self._guard_member_target(user_id, server_id, member_user_id)

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
        # Retire the target member's cached channel-view decisions so a removed
        # role's access is revoked on the very next check (no stale grant).
        get_channel_access_cache().bump(server_id, member_user_id)
        # The cached roster page embeds role ids, so a role change must evict
        # it for every viewer of this server.
        self.manager._invalidate_roster_caches(server_id)

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
        grants ``administrator`` -- including roles carrying a ``*`` wildcard
        that expands to every permission on lookup -- to prevent privilege
        escalation through self-service flows. Legacy/imported roles whose
        permission blob fails the normal validation are refused as well.

        Returns the list of role IDs actually assigned.
        """
        member = self.manager.get_member(server_id, user_id)
        if not member:
            raise MemberNotFoundError("Member not found")

        allowed = set(allowed_role_ids or [])
        now = self.manager._get_timestamp()
        assigned: List[SnowflakeID] = []

        # One audit INSERT per assigned role used to cost one INSERT plus one
        # autocommit each.  Buffer them for the duration of the loop; the
        # context manager flushes on exit (including on error), so every
        # accepted audit row still reaches srv_audit_log.
        with self.manager._buffered_audit():
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
                    try:
                        permissions = json.loads(permissions) if permissions else {}
                    except (TypeError, ValueError):
                        # A corrupt/legacy permission blob must never be treated as
                        # a harmless empty grant on a self-service path.
                        continue
                if not isinstance(permissions, dict):
                    continue
                try:
                    # Wildcards ("*", "namespace.*") expand to full grants in
                    # has_permission(). They are forbidden in freshly-written
                    # roles, but a legacy/imported row could still carry one; such
                    # a role is never safe to hand out through self-service.
                    validate_permission_dict(permissions)
                except ValueError:
                    continue
                # Checking the literal ``administrator`` key alone missed a role
                # whose permissions contain a truthy ``*`` wildcard, which grants
                # every permission (including roles.manage) on lookup.
                if has_permission(permissions, "administrator"):
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
            get_channel_access_cache().bump(server_id, user_id)
            self.manager._invalidate_roster_caches(server_id)

        return assigned
