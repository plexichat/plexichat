"""Per-channel permission overrides for roles and members."""

import json
from typing import Optional, Dict

from src.core.base import SnowflakeID

from ...channel_access_cache import get_channel_access_cache
from ...models import ChannelOverride, AuditLogAction
from ...manager.converters import _row_to_override
from ...exceptions import (
    ChannelNotFoundError,
    MemberNotFoundError,
    RoleHierarchyError,
    RoleNotFoundError,
)
from ...permission_utils import (
    can_manage_member,
    check_permissions_grantable,
    validate_permission_dict,
)
from .authz import override_target_decision
from .protocol import RoleHandlerProtocol


class RoleOverrideMixin(RoleHandlerProtocol):
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
        return _row_to_override(row) if row else None

    def _guard_override_target(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        target_type: str,
        target_id: SnowflakeID,
    ) -> None:
        """Refuse overrides on targets the actor cannot safely control.

        Two independent gaps used to sit behind ``channels.manage``:

        * Role targets: the actor could deny ``view_channel`` on a role above
          their own. That path now carries a Decision-coded hierarchy check.
          A role from another server (or one that no longer exists) is never a
          valid target either -- it used to be accepted unchecked (IDOR).
        * Member targets: no check at all was applied, so any channels.manage
          holder could deny a higher-ranked member's access, and an arbitrary
          ``target_id`` (including a user who is not a member of this server)
          was written into the override table. Membership and the usual
          ``can_manage_member`` hierarchy now both apply; self-overrides stay
          allowed for onboarding/self-service flows.
        """
        server = self.manager.get_server(server_id, user_id)
        is_owner = server is not None and server.owner_id == user_id

        if target_type == "role":
            row = self.db.fetch_one(
                "SELECT position, server_id FROM srv_roles WHERE id = ? AND deleted = 0",
                (target_id,),
            )
            if not row or row["server_id"] != server_id:
                raise RoleNotFoundError("Override target role not found")
            decision = override_target_decision(
                user_id=user_id,
                user_roles=self.manager._get_member_role_rows(server_id, user_id),
                target_position=row["position"],
                permissions=self.get_permissions(user_id, server_id),
                is_owner=is_owner,
            )
            if not decision:
                raise RoleHierarchyError("Cannot override a role at or above your own")
            return

        if target_type == "member":
            # Self-service overrides (e.g. a member allowing themselves into a
            # channel) are legitimate. Grantability of the permissions they
            # carry is enforced separately in set_channel_override.
            if target_id == user_id:
                return
            member = self.manager.get_member(server_id, target_id)
            if not member:
                raise MemberNotFoundError(
                    "Override target is not a member of this server"
                )
            if server is not None and server.owner_id == target_id:
                raise RoleHierarchyError("Cannot override the server owner")
            user_roles = self.manager._get_member_role_rows(server_id, user_id)
            target_roles = self.manager._get_member_role_rows(server_id, target_id)
            if not can_manage_member(user_roles, target_roles, is_owner, False):
                raise RoleHierarchyError(
                    "Cannot override a member at or above your own rank"
                )

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
            raise ChannelNotFoundError("Channel not found")
        # channels.manage is a server-scoped permission; resolving it with a
        # channel_id would let a per-channel override authorize rewriting this
        # (or other) channel's overrides. Check it at server scope.
        self.manager.require_permission(user_id, channel.server_id, "channels.manage")
        self._guard_override_target(user_id, channel.server_id, target_type, target_id)

        # Override allow/deny dicts carry the same power as role permissions:
        # apply_channel_overrides() folds an ``allow`` entry into the resolved
        # set, and ``administrator`` there unlocks every permission. Role CRUD
        # validates the vocabulary and the editor's own grants, but this path
        # persisted raw caller dicts, letting a channels.manage holder escalate
        # to administrator (or grant permissions they do not possess).
        validate_permission_dict(allow)
        validate_permission_dict(deny)
        check_permissions_grantable(
            self.get_permissions(user_id, channel.server_id), allow
        )

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
                (
                    override_id,
                    channel_id,
                    target_type,
                    target_id,
                    json.dumps(allow or {}),
                    json.dumps(deny or {}),
                    now,
                    now,
                ),
            )
            action = AuditLogAction.OVERRIDE_CREATE

        self.manager._log_audit(
            channel.server_id,
            user_id,
            action,
            "override",
            override_id,
            {"target_type": target_type, "target_id": target_id},
        )

        # Invalidate permissions cache
        from src.core.database import invalidate_pattern

        self.manager._cache_invalidate(self.manager._permission_cache_prefix)
        if target_type == "member":
            invalidate_pattern(f"perms:{target_id}:{channel.server_id}:*")
        else:
            # Role override affects all members with that role
            # For simplicity, we invalidate all permissions for this server/channel
            invalidate_pattern(f"perms:*:{channel.server_id}:{channel_id}")
        invalidate_pattern("has_perm:*")
        # Overrides can widen or revoke channel access. A role override affects
        # every member of the server, so bump the whole server epoch; this also
        # covers the member-target case. No stale grant may survive.
        get_channel_access_cache().bump(channel.server_id)

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
            raise ChannelNotFoundError("Channel not found")
        # channels.manage is a server-scoped permission; resolving it with a
        # channel_id would let a per-channel override authorize rewriting this
        # (or other) channel's overrides. Check it at server scope.
        self.manager.require_permission(user_id, channel.server_id, "channels.manage")
        self._guard_override_target(user_id, channel.server_id, target_type, target_id)

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
            {
                "channel_id": channel_id,
                "target_type": target_type,
                "target_id": target_id,
            },
        )

        # Invalidate permissions cache
        from src.core.database import invalidate_pattern

        self.manager._cache_invalidate(self.manager._permission_cache_prefix)
        if target_type == "member":
            invalidate_pattern(f"perms:{target_id}:{channel.server_id}:*")
        else:
            invalidate_pattern(f"perms:*:{channel.server_id}:{channel_id}")
        invalidate_pattern("has_perm:*")
        # Removing an override can revoke channel access; retire every cached
        # decision in the server so the change is visible immediately.
        get_channel_access_cache().bump(channel.server_id)

        return True
