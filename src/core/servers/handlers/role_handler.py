"""
Role and permission handler for server operations.
"""

import json
from typing import Optional, List, Dict, Any
from src.core.base import SnowflakeID
from ..models import Role, ChannelOverride, AuditLogAction
from ..exceptions import (
    RoleNotFoundError,
    RoleHierarchyError,
    DefaultRoleError,
    MemberNotFoundError,
    InvalidRoleNameError,
)
from src.core.database import cached
from ..permissions import (
    calculate_base_permissions,
    apply_channel_overrides,
    has_permission as check_permission,
    can_manage_role,
)

class RoleHandler:
    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db

    def validate_role_name(self, name: str) -> str:
        """Validate and sanitize role name."""
        if not name or not name.strip():
            raise InvalidRoleNameError("Role name cannot be empty")

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
        name = self.validate_role_name(name)

        pos_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position), 0) + 1 as next_pos FROM srv_roles WHERE server_id = ? AND deleted = 0",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 1

        now = self.manager._get_timestamp()
        role_id = self.manager._generate_id()

        self.db.execute(
            """INSERT INTO srv_roles 
               (id, server_id, name, permissions, color, hoist, mentionable, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )

        self.manager._log_audit(server_id, user_id, AuditLogAction.ROLE_CREATE, "role", role_id)
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
        return self.manager._row_to_role(row)

    def get_roles(self, user_id: SnowflakeID, server_id: SnowflakeID) -> List[Role]:
        """Get all roles in a server."""
        rows = self.db.fetch_all(
            "SELECT * FROM srv_roles WHERE server_id = ? AND deleted = 0 ORDER BY position DESC",
            (server_id,),
        )
        return [self.manager._row_to_role(row) for row in rows]

    def update_role(
        self,
        user_id: SnowflakeID,
        role_id: SnowflakeID,
        name: Optional[str] = None,
        permissions: Optional[Dict[str, bool]] = None,
        color: Optional[str] = None,
        hoist: Optional[bool] = None,
        mentionable: Optional[bool] = None,
    ) -> Role:
        """Update role settings."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")

        # Check hierarchy
        user_roles = self.manager._get_member_role_rows(role.server_id, user_id)
        server = self.manager.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError("Cannot modify a role at or above your highest role")

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

        if updates:
            updates.append("updated_at = ?")
            params.append(self.manager._get_timestamp())
            params.append(role_id)

            self.db.execute(
                f"UPDATE srv_roles SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            self.manager._log_audit(
                role.server_id,
                user_id,
                AuditLogAction.ROLE_UPDATE,
                "role",
                role_id,
                changes,
            )

        updated_role = self.get_role(role_id, user_id)
        assert updated_role is not None
        return updated_role

    def delete_role(self, user_id: SnowflakeID, role_id: SnowflakeID) -> bool:
        """Delete a role."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")

        if role.is_default:
            raise DefaultRoleError("Cannot delete the default role")

        # Check hierarchy
        user_roles = self.manager._get_member_role_rows(role.server_id, user_id)
        server = self.manager.get_server(role.server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if not can_manage_role(user_roles, {"position": role.position}, is_owner):
            raise RoleHierarchyError("Cannot delete a role at or above your highest role")

        self.db.execute("UPDATE srv_roles SET deleted = 1 WHERE id = ?", (role_id,))
        self.db.execute("DELETE FROM srv_member_roles WHERE role_id = ?", (role_id,))

        self.manager._log_audit(role.server_id, user_id, AuditLogAction.ROLE_DELETE, "role", role_id)
        return True

    def move_role(
        self, user_id: SnowflakeID, role_id: SnowflakeID, position: int
    ) -> Role:
        """Move a role to a new position in hierarchy."""
        role = self.get_role(role_id, user_id)
        if not role:
            raise RoleNotFoundError("Role not found")

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

        result = self.get_role(role_id, user_id)
        assert result is not None
        return result

    def get_channel_override(
        self,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> Optional[ChannelOverride]:
        """Get permission override for a channel."""
        row = self.db.fetch_one(
            """SELECT * FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = ? AND target_id = ?""",
            (channel_id, target_type, target_id),
        )
        return self.manager._row_to_override(row) if row else None

    def set_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
        allow: Optional[Dict[str, bool]] = None,
        deny: Optional[Dict[str, bool]] = None,
    ) -> ChannelOverride:
        """Set permission override for a channel."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            from ..exceptions import ChannelNotFoundError
            raise ChannelNotFoundError("Channel not found")

        now = self.manager._get_timestamp()
        existing = self.db.fetch_one(
            """SELECT id FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = ? AND target_id = ?""",
            (channel_id, target_type, target_id),
        )

        if existing:
            self.db.execute(
                """UPDATE srv_channel_overrides 
                   SET allow = ?, deny = ?, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(allow or {}), json.dumps(deny or {}), now, existing["id"]),
            )
            override_id = existing["id"]
            action = AuditLogAction.OVERRIDE_UPDATE
        else:
            override_id = self.manager._generate_id()
            self.db.execute(
                """INSERT INTO srv_channel_overrides 
                   (id, channel_id, target_type, target_id, allow, deny, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (override_id, channel_id, target_type, target_id, json.dumps(allow or {}), json.dumps(deny or {}), now, now),
            )
            action = AuditLogAction.OVERRIDE_CREATE

        self.manager._log_audit(channel.server_id, user_id, action, "override", override_id, {"target_type": target_type, "target_id": target_id})
        
        # Invalidate permissions cache
        from src.core.database import invalidate_pattern
        if target_type == 'member':
            invalidate_pattern(f"perms:{target_id}:{channel.server_id}:*")
        else:
            # Role override affects all members with that role
            # For simplicity, we invalidate all permissions for this server/channel
            invalidate_pattern(f"perms:*:{channel.server_id}:{channel_id}")

        result = self.get_channel_override(channel_id, target_type, target_id)
        assert result is not None
        return result

    def delete_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> bool:
        """Delete a permission override."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            from ..exceptions import ChannelNotFoundError
            raise ChannelNotFoundError("Channel not found")

        self.db.execute(
            """DELETE FROM srv_channel_overrides 
               WHERE channel_id = ? AND target_type = ? AND target_id = ?""",
            (channel_id, target_type, target_id),
        )

        self.manager._log_audit(
            channel.server_id,
            user_id,
            AuditLogAction.OVERRIDE_DELETE,
            "override",
            None,
            {"channel_id": channel_id, "target_type": target_type, "target_id": target_id},
        )

        # Invalidate permissions cache
        from src.core.database import invalidate_pattern
        if target_type == 'member':
            invalidate_pattern(f"perms:{target_id}:{channel.server_id}:*")
        else:
            invalidate_pattern(f"perms:*:{channel.server_id}:{channel_id}")

        return True

    def get_permissions_batch(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_ids: List[SnowflakeID],
    ) -> Dict[SnowflakeID, Dict[str, bool]]:
        """Get permissions for multiple channels in a server."""
        if not channel_ids:
            return {}

        # 1. Base permissions
        # This uses existing cache/logic for base perms
        base_perms = self.get_permissions(user_id, server_id)
        
        # If admin, return all true for all channels
        if base_perms.get("administrator"):
             return {cid: base_perms for cid in channel_ids}

        # 2. Get user's roles
        role_rows = self.manager._get_member_role_rows(server_id, user_id)
        role_ids = [r["id"] for r in role_rows]

        # 3. Fetch overrides
        placeholders = ",".join("?" * len(channel_ids))
        query_params = list(channel_ids)
        
        or_parts = ["(target_type = 'member' AND target_id = ?)"]
        query_params.append(user_id)
        
        if role_ids:
            r_placeholders = ",".join("?" * len(role_ids))
            or_parts.append(f"(target_type = 'role' AND target_id IN ({r_placeholders}))")
            query_params.extend(role_ids)
            
        query = f"""SELECT * FROM srv_channel_overrides 
                    WHERE channel_id IN ({placeholders}) 
                    AND ({' OR '.join(or_parts)})"""
        
        overrides_rows = self.db.fetch_all(query, tuple(query_params))
        
        # Group overrides by channel
        channel_overrides: Dict[SnowflakeID, List[Dict[str, Any]]] = {cid: [] for cid in channel_ids}
        member_overrides: Dict[SnowflakeID, Dict[str, Any]] = {}
        
        for row in overrides_rows:
            cid = row["channel_id"]
            if cid in channel_overrides:
                if row["target_type"] == "member":
                    member_overrides[cid] = dict(row)
                else:
                    channel_overrides[cid].append(dict(row))
                
        # 4. Calculate permissions for each channel
        result = {}
        for cid in channel_ids:
            perms = apply_channel_overrides(base_perms, channel_overrides[cid], member_overrides.get(cid))
            result[cid] = perms
            
            # Populate cache
            cache_key = (user_id, server_id, cid)
            self.manager._cache_set(self.manager._permission_cache, cache_key, perms)
            
        return result

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        """Get all permissions for a user in a server/channel (cached in Redis)."""
        cache_key = f"perms:{user_id}:{server_id}:{channel_id or 0}"
        
        # 1. Try internal memory first
        mem_cached = self.manager._cache_get(self.manager._permission_cache, cache_key)
        if mem_cached is not None:
            return mem_cached

        # 2. Try Redis
        from src.core.database import cache_get, cache_set, redis_available
        if redis_available():
            redis_cached = cache_get(cache_key)
            if redis_cached is not None:
                if isinstance(redis_cached, str):
                    perms = json.loads(redis_cached)
                else:
                    perms = redis_cached
                self.manager._cache_set(self.manager._permission_cache, cache_key, perms)
                return perms

        # 3. Calculate permissions
        owner_id = self.manager._cache_get(self.manager._server_owner_cache, server_id)
        if owner_id is None:
            server_row = self.db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                (server_id,),
            )
            if not server_row:
                return {}
            owner_id = int(server_row["owner_id"])
            self.manager._cache_set(self.manager._server_owner_cache, server_id, owner_id)

        is_owner = int(owner_id) == int(user_id)
        role_rows = self.manager._get_member_role_rows(server_id, user_id)
        base_perms = calculate_base_permissions(role_rows, is_owner)

        result = base_perms
        if channel_id:
            member = self.manager.get_member(server_id, user_id)
            if not member:
                return {}

            role_ids = [r["id"] for r in role_rows]
            role_overrides = []
            if role_ids:
                placeholders = ",".join("?" * len(role_ids))
                override_rows = self.db.fetch_all(
                    f"""SELECT * FROM srv_channel_overrides 
                       WHERE channel_id = ? AND target_type = 'role' AND target_id IN ({placeholders})""",
                    (channel_id, *role_ids),
                )
                role_overrides = [dict(row) for row in override_rows]

            member_override_row = self.db.fetch_one(
                """SELECT * FROM srv_channel_overrides 
                   WHERE channel_id = ? AND target_type = 'member' AND target_id = ?""",
                (channel_id, user_id),
            )
            member_override = dict(member_override_row) if member_override_row else None

            result = apply_channel_overrides(base_perms, role_overrides, member_override)

        # 4. Cache result
        self.manager._cache_set(self.manager._permission_cache, cache_key, result)
        if redis_available():
            cache_set(cache_key, result, ttl=60)
            
        return result

    @cached(ttl=10, prefix="has_perm")
    def has_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        permission: str,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if a user has a permission in a server/channel."""
        permissions = self.get_permissions(user_id, server_id, channel_id)
        return check_permission(permissions, permission)

    def get_member_roles(self, server_id: SnowflakeID, member_user_id: SnowflakeID) -> List[Role]:
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
        return [self.manager._row_to_role(row) for row in rows]

    def assign_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Assign a role to a member."""
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
            raise RoleHierarchyError("Cannot assign a role at or above your highest role")

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

        for key in list(self.manager._permission_cache.keys()):
            if key[0] == member_user_id and key[1] == server_id:
                self.manager._cache_invalidate(self.manager._permission_cache, key)

        # Invalidate Redis
        from src.core.database import invalidate_pattern
        invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")

        self.manager._log_audit(server_id, user_id, AuditLogAction.MEMBER_ROLE_ADD, "member", member_user_id, {"role_id": role_id})
        return True

    def remove_role(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        member_user_id: SnowflakeID,
        role_id: SnowflakeID,
    ) -> bool:
        """Remove a role from a member."""
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
            raise RoleHierarchyError("Cannot remove a role at or above your highest role")

        self.db.execute("DELETE FROM srv_member_roles WHERE member_id = ? AND role_id = ?", (member.id, role_id))

        for key in list(self.manager._permission_cache.keys()):
            if key[0] == member_user_id and key[1] == server_id:
                self.manager._cache_invalidate(self.manager._permission_cache, key)

        # Invalidate Redis
        from src.core.database import invalidate_pattern
        invalidate_pattern(f"perms:{member_user_id}:{server_id}:*")

        self.manager._log_audit(server_id, user_id, AuditLogAction.MEMBER_ROLE_REMOVE, "member", member_user_id, {"role_id": role_id})
        return True
