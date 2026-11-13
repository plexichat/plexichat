"""Per-channel permission overrides for roles and members."""

import json
from typing import Optional, Dict

from src.core.base import SnowflakeID

from ...models import ChannelOverride, AuditLogAction
from ...manager.converters import _row_to_override
from ...exceptions import ChannelNotFoundError
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
        self.manager.require_permission(
            user_id, channel.server_id, "channels.manage", channel_id
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
        self.manager.require_permission(
            user_id, channel.server_id, "channels.manage", channel_id
        )

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

        return True
