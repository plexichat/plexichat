"""Effective permission resolution, caching and permission checks."""

import json
from typing import Optional, List, Dict, Any

from src.core.base import SnowflakeID
from src.core.database import cached

from ...permission_utils import (
    calculate_base_permissions,
    apply_channel_overrides,
    has_permission as check_permission,
)
from .protocol import RoleHandlerProtocol


class RolePermissionMixin(RoleHandlerProtocol):
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

        # Validate role_ids are integers to prevent SQL injection
        if role_ids and not all(isinstance(rid, int) for rid in role_ids):
            raise ValueError("All role IDs must be integers")

        # Role-position map so overrides can be applied LOWEST position
        # first: a higher-position role's deny must beat a lower role's
        # allow (see apply_channel_overrides ordering contract).
        role_positions = {int(r["id"]): int(r.get("position") or 0) for r in role_rows}

        # Avoid dynamic IN clause to satisfy bandit - use individual queries
        overrides_rows = []
        for channel_id in channel_ids:
            # Check member override
            member_row = self.db.fetch_one(
                "SELECT * FROM srv_channel_overrides WHERE channel_id = ? AND target_type = 'member' AND target_id = ?",
                (channel_id, user_id),
            )
            if member_row:
                overrides_rows.append(member_row)
            # Check role overrides if any
            if role_ids:
                for role_id in role_ids:
                    role_row = self.db.fetch_one(
                        "SELECT * FROM srv_channel_overrides WHERE channel_id = ? AND target_type = 'role' AND target_id = ?",
                        (channel_id, role_id),
                    )
                    if role_row:
                        overrides_rows.append(role_row)

        # Group overrides by channel
        channel_overrides: Dict[SnowflakeID, List[Dict[str, Any]]] = {
            cid: [] for cid in channel_ids
        }
        member_overrides: Dict[SnowflakeID, Dict[str, Any]] = {}

        for row in overrides_rows:
            cid = row["channel_id"]
            if cid in channel_overrides:
                if row["target_type"] == "member":
                    member_overrides[cid] = dict(row)
                else:
                    channel_overrides[cid].append(dict(row))

        # Lowest-position role first so the highest role's decision wins.
        for cid in channel_overrides:
            channel_overrides[cid].sort(
                key=lambda o: role_positions.get(int(o.get("target_id") or 0), 0)
            )

        # 4. Calculate permissions for each channel
        result = {}
        for cid in channel_ids:
            perms = apply_channel_overrides(
                base_perms, channel_overrides[cid], member_overrides.get(cid)
            )
            result[cid] = perms

            # Populate cache
            cache_key = (user_id, server_id, cid)
            self.manager._cache_set(
                self.manager._permission_cache_prefix, cache_key, perms
            )

        return result

    def get_permissions(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> Dict[str, bool]:
        """Get all permissions for a user in a server/channel (cached in Valkey)."""
        cache_key = f"perms:{user_id}:{server_id}:{channel_id or 0}"

        # 1. Try internal memory first
        mem_cached = self.manager._cache_get(
            self.manager._permission_cache_prefix, cache_key
        )
        if mem_cached is not None:
            return mem_cached

        # 2. Try Valkey
        from src.core.database import cache_get, cache_set, valkey_available

        if valkey_available():
            valkey_cached = cache_get(cache_key)
            if valkey_cached is not None:
                if isinstance(valkey_cached, str):
                    perms = json.loads(valkey_cached)
                else:
                    perms = valkey_cached
                self.manager._cache_set(
                    self.manager._permission_cache_prefix, cache_key, perms
                )
                return perms

        # 3. Calculate permissions
        owner_id = self.manager._cache_get(
            self.manager._server_owner_cache_prefix, server_id
        )
        if owner_id is None:
            server_row = self.db.fetch_one(
                "SELECT owner_id FROM srv_servers WHERE id = ? AND deleted = 0",
                (server_id,),
            )
            if not server_row:
                return {}
            owner_id = int(server_row["owner_id"])
            self.manager._cache_set(
                self.manager._server_owner_cache_prefix, server_id, owner_id
            )

        is_owner = int(owner_id) == int(user_id)
        role_rows = self.manager._get_member_role_rows(server_id, user_id)
        base_perms = calculate_base_permissions(role_rows, is_owner)

        # The server owner is an administrator even if a legacy or partially
        # restored database is missing the owner's membership row. Keep the
        # base permissions in that case, but do not return early: timeout
        # restrictions below must still be evaluated.
        result = base_perms
        if channel_id:
            member = self.manager.get_member(server_id, user_id)
            if not member:
                if not is_owner:
                    return {}
                member = None
            if member is None:
                role_ids = []
                role_overrides = []
                member_override = None
            else:
                role_ids = [r["id"] for r in role_rows]
                role_overrides = []
                if role_ids:
                    if not all(isinstance(rid, int) for rid in role_ids):
                        raise ValueError("All role IDs must be integers")
                    override_rows = []
                    for role_id in role_ids:
                        row = self.db.fetch_one(
                            "SELECT * FROM srv_channel_overrides WHERE channel_id = ? AND target_type = 'role' AND target_id = ?",
                            (channel_id, role_id),
                        )
                        if row:
                            override_rows.append(row)
                    # Lowest-position role first so a higher role's deny
                    # beats a lower role's allow (apply_channel_overrides
                    # applies them in the order given).
                    role_positions = {
                        int(r["id"]): int(r.get("position") or 0) for r in role_rows
                    }
                    role_overrides = sorted(
                        (dict(row) for row in override_rows),
                        key=lambda o: role_positions.get(
                            int(o.get("target_id") or 0), 0
                        ),
                    )

                member_override_row = self.db.fetch_one(
                    """SELECT * FROM srv_channel_overrides
                       WHERE channel_id = ? AND target_type = 'member' AND target_id = ?""",
                    (channel_id, user_id),
                )
                member_override = (
                    dict(member_override_row) if member_override_row else None
                )

                result = apply_channel_overrides(
                    base_perms, role_overrides, member_override
                )

        # 4. Check for timeout. Channel overrides (if any) were already applied
        #    above; here we only revoke the interaction permissions of a timed-out
        #    member on top of the resolved permission set.
        # get_member() is @cached and may hand back a plain dict on cache hits,
        # so normalize before touching attributes.
        member = self.manager.get_member(server_id, user_id)
        if isinstance(member, dict):
            timeout_until = member.get("timeout_until")
        else:
            timeout_until = getattr(member, "timeout_until", None)
        if timeout_until:
            now = self.manager._get_timestamp()
            if timeout_until > now:
                # User is timed out, revoke interaction permissions
                restricted_perms = [
                    "messages.send",
                    "messages.add_reactions",
                    "voice.speak",
                    "voice.connect",
                    "messages.attach_files",
                    "messages.embed_links",
                ]
                for perm in restricted_perms:
                    result[perm] = False

        # 5. Cache result
        self.manager._cache_set(
            self.manager._permission_cache_prefix, cache_key, result
        )
        if valkey_available():
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
