"""Role CRUD: creation, lookup, update, deletion and hierarchy moves."""

import json
from typing import Optional, List, Dict

from src.core.base import SnowflakeID
from src.core.security.hardening import HardeningViolation
from src.core.security.hardening.text import reject_control_characters

from ...models import Role, AuditLogAction
from ...manager.converters import _row_to_role
from ...exceptions import (
    RoleNotFoundError,
    RoleHierarchyError,
    DefaultRoleError,
    InvalidRoleNameError,
    ServerLimitReachedError,
)
from ...permission_utils import (
    can_manage_role,
    get_highest_role_position,
    validate_permission_dict,
    check_permissions_grantable,
)
from .authz import role_edit_decision
from .protocol import RoleHandlerProtocol


# One statement re-checks the per-server role cap at the instant of the insert.
# The old flow ran a separate ``SELECT COUNT(*)`` for ``max_roles_per_server``
# and only then issued a separate bare ``INSERT``; two concurrent creators could
# both read a below-cap count and both insert, pushing a server over
# ``max_roles_per_server`` (a TOCTOU quota bypass). The guard subquery sees the
# table state *before* the new row exists, so exactly the callers that still fit
# get a row and ``rowcount == 0`` is an authoritative "limit reached" signal.
# SQLite serialises this single statement with its write lock; PostgreSQL's
# READ COMMITTED snapshot would let two statements both see a stale count, so
# create_role() additionally takes an advisory xact lock (see there).
_ATOMIC_ROLE_INSERT_SQL = """
INSERT INTO srv_roles
    (id, server_id, name, permissions, color, hoist, mentionable, position,
     created_at, updated_at)
SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
WHERE (SELECT COUNT(*) FROM srv_roles WHERE server_id = ? AND deleted = 0) < ?
"""


class RoleCrudMixin(RoleHandlerProtocol):
    def validate_role_name(self, name: str) -> str:
        """Validate and sanitize role name."""
        if not name or not name.strip():
            raise InvalidRoleNameError("Role name cannot be empty")

        try:
            name = reject_control_characters(name, "Role name")
        except HardeningViolation as exc:
            raise InvalidRoleNameError(str(exc), name) from exc

        name = name.strip()
        max_len = self.manager._config.get("role_name_max_length", 100)
        if len(name) > max_len:
            raise InvalidRoleNameError(
                f"Role name cannot exceed {max_len} characters", name
            )
        return name

    def create_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: bool = False,
        mentionable: bool = False,
    ) -> Role:
        """Create a new role in a server."""
        self.manager.require_permission(user_id, server_id, "roles.manage")
        name = self.validate_role_name(name)

        # Permission dicts are validated against the SERVER_PERMISSIONS
        # vocabulary; wildcards ("*", "namespace.*") must never be persisted.
        validate_permission_dict(permissions)
        # A role editor may only grant permissions they possess themselves,
        # otherwise any roles.manage holder could self-escalate to
        # administrator.
        editor_perms = self.get_permissions(user_id, server_id)
        check_permissions_grantable(editor_perms, permissions)

        # L2: the configured per-server role cap. This early read is only
        # advisory feedback (it avoids an expensive insert for an obviously
        # full server); it is NOT the authoritative check. Two concurrent
        # creators can both read a below-cap count, so the atomic guarded
        # INSERT below is what actually enforces `max_roles_per_server`.
        max_roles = self.manager._config.get("max_roles_per_server", 250)
        count_row = self.db.fetch_one(
            "SELECT COUNT(*) as count FROM srv_roles "
            "WHERE server_id = ? AND deleted = 0",
            (server_id,),
        )
        role_count = count_row["count"] if count_row else 0
        if role_count >= max_roles:
            raise ServerLimitReachedError(
                f"Server has reached the maximum of {max_roles} roles",
                limit=max_roles,
            )

        pos_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position), 0) + 1 as next_pos FROM srv_roles WHERE server_id = ? AND deleted = 0",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 1

        # Hierarchy on creation: a non-owner cannot create a role at or above
        # their own highest role, so clamp the new role below it.
        server_row = self.manager.get_server(server_id, user_id)
        is_owner = server_row is not None and server_row.owner_id == user_id
        if not is_owner:
            user_roles = self.manager._get_member_role_rows(server_id, user_id)
            user_highest = get_highest_role_position(user_roles)
            if user_highest > 0 and position >= user_highest:
                position = user_highest - 1

        now = self.manager._get_timestamp()
        role_id = self.manager._generate_id()

        # PostgreSQL READ COMMITTED would let two concurrent guarded INSERTs
        # both see a stale count. Serialise per server with an advisory xact
        # lock. Namespace 141 stays clear of the invite (1/2), emoji (100/101),
        # sticker (102), soundboard (120), webhook (130) and scheduled-event
        # (140) quota locks; masking to 30 bits keeps the second key in signed
        # int4 range.
        is_postgres = getattr(self.db, "type", "") == "postgres"
        if is_postgres:
            self.db.begin_transaction()
            try:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (141, int(server_id) & 0x3FFFFFFF),
                )
            except Exception:
                self.db.rollback()
                raise
        try:
            if not is_postgres:
                self.db.begin_transaction()

            cursor = self.db.execute(
                _ATOMIC_ROLE_INSERT_SQL,
                (
                    role_id,
                    server_id,
                    name,
                    json.dumps(permissions or {}),
                    color,
                    1 if hoist else 0,
                    1 if mentionable else 0,
                    position,
                    now,
                    now,
                    server_id,
                    max_roles,
                ),
            )
            # rowcount 0 means the count guard found the server already at (or
            # over) its role cap at insert time — a concurrent creator won the
            # race. Roll back and surface the same quota error the advisory
            # check would have raised.
            if getattr(cursor, "rowcount", 0) == 0:
                raise ServerLimitReachedError(
                    f"Server has reached the maximum of {max_roles} roles",
                    limit=max_roles,
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

        self.manager._log_audit(
            server_id, user_id, AuditLogAction.ROLE_CREATE, "role", role_id
        )
        result = self.manager.get_role(role_id, user_id)
        assert result is not None
        return result

    def get_role(self, role_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Role]:
        """Get a role by ID."""
        row = self.db.fetch_one(
            "SELECT * FROM srv_roles WHERE id = ? AND deleted = 0",
            (role_id,),
        )
        if not row:
            return None
        if not self.manager._is_member(row["server_id"], user_id):
            return None
        return _row_to_role(row)

    def get_roles(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Role]:
        """Get all roles in a server."""
        self.manager.require_permission(user_id, server_id, "roles.manage")
        rows = self.db.fetch_all(
            "SELECT * FROM srv_roles WHERE server_id = ? AND deleted = 0 ORDER BY position DESC",
            (server_id,),
        )
        return [_row_to_role(row) for row in rows]

    def update_role(
        self,
        user_id: SnowflakeID,
        role_id: SnowflakeID,
        name: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: Optional[bool] = None,
        mentionable: Optional[bool] = None,
        position: Optional[int] = None,
    ) -> Role:
        """Update role settings."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")
        self.manager.require_permission(user_id, role.server_id, "roles.manage")

        # Check hierarchy
        user_roles = self.manager._get_member_role_rows(role.server_id, user_id)
        server = self.manager.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        # The hierarchy rule is a Decision now, so a refusal carries which rule
        # fired (capability_missing / rank_too_low) instead of one generic error.
        decision = role_edit_decision(
            user_id=user_id,
            user_roles=user_roles,
            target_position=role.position,
            permissions=self.get_permissions(user_id, role.server_id),
            is_owner=is_owner,
        )
        if not decision:
            raise RoleHierarchyError(
                "Cannot modify a role at or above your highest role"
            )

        if permissions is not None:
            validate_permission_dict(permissions)
            editor_perms = self.get_permissions(user_id, role.server_id)
            check_permissions_grantable(editor_perms, permissions)

        if position is not None and position != role.position:
            if role.is_default:
                raise DefaultRoleError("Cannot move the default role")
            user_highest = get_highest_role_position(user_roles)
            if not is_owner and position >= user_highest:
                raise RoleHierarchyError(
                    "Cannot move a role to or above your highest role"
                )

        updates = []
        params = []
        changes = {}

        if name is not None:
            if role.is_default:
                raise DefaultRoleError("Cannot rename the default role")
            name = self.validate_role_name(name)
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": role.name, "new": name}

        if permissions is not None:
            updates.append("permissions = ?")
            params.append(json.dumps(permissions))
            changes["permissions"] = {"old": role.permissions, "new": permissions}

        if color is not None:
            updates.append("color = ?")
            params.append(color)
            changes["color"] = {"old": role.color, "new": color}

        if hoist is not None:
            updates.append("hoist = ?")
            params.append(1 if hoist else 0)
            changes["hoist"] = {"old": role.hoist, "new": hoist}

        if mentionable is not None:
            updates.append("mentionable = ?")
            params.append(1 if mentionable else 0)
            changes["mentionable"] = {"old": role.mentionable, "new": mentionable}

        if position is not None and position != role.position:
            updates.append("position = ?")
            params.append(position)
            changes["position"] = {"old": role.position, "new": position}

        if updates:
            # Whitelist of allowed column names for UPDATE. ``updated_at`` is
            # deliberately absent: every statement below already writes it, so
            # keeping an ``updated_at = ?`` entry here would emit
            # "SET updated_at = ?, updated_at = ?", which PostgreSQL rejects
            # with "multiple assignments to same column".
            allowed_columns = {
                "name",
                "color",
                "hoist",
                "mentionable",
                "permissions",
                "position",
            }

            # Single atomic UPDATE: one statement assigns every changed
            # column plus updated_at, so a mid-update failure cannot leave a
            # half-written row and the write costs one round trip instead of
            # one per column. Column names come only from the allow-list
            # (values stay parameterized) and the SET clause is joined, so
            # the static SQL-guard audit keeps passing.
            set_columns = []
            set_params = []
            for update, value in zip(updates, params):
                col_name = update.split(" = ")[0]
                if col_name not in allowed_columns:
                    raise ValueError(f"Invalid column name: {col_name}")
                set_columns.append(col_name)
                set_params.append(value)
            now = self.manager._get_timestamp()
            set_columns.append("updated_at")
            set_params.append(now)
            set_clause = ", ".join(f"{col} = ?" for col in set_columns)
            # Re-assert the position the hierarchy decision was made against at
            # write time. Without this CAS predicate the read-then-write window
            # lets a concurrent promotion turn a previously-authorized edit of
            # a lower role into an edit of a role at/above the actor's rank.
            query = (
                "UPDATE srv_roles SET " + set_clause + " WHERE id = ? AND position = ?"
            )

            # A role update rewrites the permission bits that gate both
            # ``events.manage`` and ``server.manage``. Take the SAME advisory
            # locks those gated writes take, keyed by the same server_id, so a
            # re-check + write in delete_event/update_server cannot race this
            # revocation. Both locks are always acquired in ascending order
            # (142 then 143); gated writers take only one of the two, so there
            # is no lock-order cycle (deadlock avoidance). SQLite serialises
            # writers with its own write lock.
            is_postgres = getattr(self.db, "type", "") == "postgres"
            if is_postgres:
                self.db.begin_transaction()
                try:
                    self.db.execute(
                        "SELECT pg_advisory_xact_lock(?, ?)",
                        (142, int(role.server_id) & 0x3FFFFFFF),
                    )
                    self.db.execute(
                        "SELECT pg_advisory_xact_lock(?, ?)",
                        (143, int(role.server_id) & 0x3FFFFFFF),
                    )
                except Exception:
                    self.db.rollback()
                    raise
            try:
                if not is_postgres:
                    self.db.begin_transaction()

                cursor = self.db.execute(query, (*set_params, role_id, role.position))
                rowcount = getattr(cursor, "rowcount", None)
                if isinstance(rowcount, int) and rowcount <= 0:
                    raise RoleHierarchyError("Role changed concurrently, retry")

                self.manager._log_audit(
                    role.server_id,
                    user_id,
                    AuditLogAction.ROLE_UPDATE,
                    "role",
                    role_id,
                    changes,
                )

                # Role permission changes affect every member holding the role,
                # so the cached permission snapshots for the whole server must
                # be dropped or revocations would not take effect until TTL.
                self.manager._invalidate_server_permission_caches(role.server_id)
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

        updated_role = self.get_role(role_id, user_id)
        assert updated_role is not None
        return updated_role

    def delete_role(self, user_id: SnowflakeID, role_id: SnowflakeID) -> bool:
        """Delete a role."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")
        self.manager.require_permission(user_id, role.server_id, "roles.manage")

        if role.is_default:
            raise DefaultRoleError("Cannot delete the default role")

        # Check hierarchy
        user_roles = self.manager._get_member_role_rows(role.server_id, user_id)
        server = self.manager.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError(
                "Cannot delete a role at or above your highest role"
            )

        # Re-assert the position the hierarchy decision was made against at
        # write time, mirroring update_role. Without this CAS predicate a
        # concurrent promotion between the check above and this write turns a
        # previously-authorized deletion of a lower role into the deletion of a
        # role now at/above the actor's rank.
        # Deleting a role revokes its bits for every holder, gating both
        # ``events.manage`` and ``server.manage``. Take the same two advisory
        # locks as those gated writes, in the fixed 142 -> 143 order, so no
        # re-check + write can race this revocation.
        is_postgres = getattr(self.db, "type", "") == "postgres"
        if is_postgres:
            self.db.begin_transaction()
            try:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (142, int(role.server_id) & 0x3FFFFFFF),
                )
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    (143, int(role.server_id) & 0x3FFFFFFF),
                )
            except Exception:
                self.db.rollback()
                raise
        try:
            if not is_postgres:
                self.db.begin_transaction()

            cursor = self.db.execute(
                "UPDATE srv_roles SET deleted = 1 WHERE id = ? AND position = ?",
                (role_id, role.position),
            )
            rowcount = getattr(cursor, "rowcount", None)
            if isinstance(rowcount, int) and rowcount <= 0:
                raise RoleHierarchyError("Role changed concurrently, retry")

            self.db.execute(
                "DELETE FROM srv_member_roles WHERE role_id = ?", (role_id,)
            )

            # Members who held the deleted role lose its permissions
            # immediately; drop the cached snapshots so they are recomputed.
            self.manager._invalidate_server_permission_caches(role.server_id)

            self.manager._log_audit(
                role.server_id, user_id, AuditLogAction.ROLE_DELETE, "role", role_id
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return True

    def move_role(
        self, user_id: SnowflakeID, role_id: SnowflakeID, position: int
    ) -> Role:
        """Move a role to a new position in hierarchy."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")
        self.manager.require_permission(user_id, role.server_id, "roles.manage")

        if role.is_default:
            raise DefaultRoleError("Cannot move the default role")

        # Check hierarchy
        user_roles = self.manager._get_member_role_rows(role.server_id, user_id)
        server = self.manager.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError("Cannot move a role at or above your highest role")

        user_highest = max((r.get("position", 0) for r in user_roles), default=0)
        if position >= user_highest and not is_owner:
            raise RoleHierarchyError("Cannot move role above your highest role")

        self.db.execute(
            "UPDATE srv_roles SET position = ?, updated_at = ? WHERE id = ?",
            (position, self.manager._get_timestamp(), role_id),
        )

        # Position is the tie-breaker for channel overrides (a higher role's
        # deny wins an allow from a lower role) and for role hierarchy. Moving
        # a role therefore changes resolved permissions for every member who
        # holds it, so retire the server's cached snapshots and decision epochs
        # or the old ordering keeps serving stale grants/denials until TTL.
        self.manager._invalidate_server_permission_caches(role.server_id)

        result = self.get_role(role_id, user_id)
        assert result is not None
        return result
