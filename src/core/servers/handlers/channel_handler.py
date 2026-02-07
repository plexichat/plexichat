"""
Channel and category handler for server operations.
"""

import re
from typing import Optional, List, Union
from src.core.base import SnowflakeID
from ..models import Channel, ChannelCategory, ChannelType, AuditLogAction
from ..exceptions import (
    ServerNotFoundError,
    ChannelNotFoundError,
    ChannelTypeError,
    CategoryNotFoundError,
    InvalidChannelNameError,
)
from src.core.database import cache_delete, redis_available

class ChannelHandler:
    def __init__(self, manager):
        self.manager = manager
        self.db = manager._db

    def validate_channel_name(
        self, name: str, channel_type: ChannelType = ChannelType.TEXT
    ) -> str:
        """Validate and sanitize channel name."""
        if not name or not name.strip():
            raise InvalidChannelNameError("Channel name cannot be empty")

        name = name.strip()
        
        # Strict ASCII-only hyphenated naming for text-based channels
        if channel_type in (ChannelType.TEXT, ChannelType.ANNOUNCEMENT):
            name = name.lower()
            name = re.sub(r"[^a-z0-9]+", "-", name)
            name = name.strip("-")
            
            if not name:
                raise InvalidChannelNameError("Channel name must contain alphanumeric characters")
        else:
            name = re.sub(r"\s+", " ", name)
            name = re.sub(r"[^\x20-\x7E]", "", name)
            name = name.strip()

        max_len = self.manager._config.get("channel_name_max_length", 100)
        if len(name) > max_len:
            raise InvalidChannelNameError(
                f"Channel name cannot exceed {max_len} characters", name
            )

        return name

    def create_channel(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
        channel_type: Union[ChannelType, str] = ChannelType.TEXT,
        category_id: Optional[SnowflakeID] = None,
        topic: Optional[str] = None,
        nsfw: bool = False,
        slowmode_seconds: int = 0,
    ) -> Channel:
        """Create a new channel in a server."""
        if isinstance(channel_type, str):
            try:
                channel_type = ChannelType(channel_type.lower())
            except ValueError:
                channel_type = ChannelType.TEXT

        name = self.validate_channel_name(name, channel_type)

        if category_id:
            cat = self.db.fetch_one(
                "SELECT 1 FROM srv_categories WHERE id = ? AND server_id = ?",
                (category_id, server_id),
            )
            if not cat:
                raise CategoryNotFoundError("Category not found")

        pos_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM srv_channels WHERE server_id = ?",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 0

        now = self.manager._get_timestamp()
        channel_id = self.manager._generate_id()

        self.db.execute(
            """INSERT INTO srv_channels 
               (id, server_id, name, channel_type, category_id, position, topic, nsfw, slowmode_seconds, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                server_id,
                name,
                channel_type.value,
                category_id,
                position,
                topic,
                1 if nsfw else 0,
                slowmode_seconds,
                now,
                now,
            ),
        )

        if channel_type == ChannelType.TEXT and self.manager._messaging:
            conv = (
                self.manager._messaging.create_server_channel_conversation(
                    server_id, channel_id
                )
                if hasattr(self.manager._messaging, "create_server_channel_conversation")
                else None
            )
            if conv:
                self.db.execute(
                    "UPDATE srv_channels SET conversation_id = ? WHERE id = ?",
                    (conv.id, channel_id),
                )

        self.manager._log_audit(
            server_id,
            user_id,
            AuditLogAction.CHANNEL_CREATE,
            "channel",
            channel_id,
        )

        if redis_available():
            cache_delete(f"server_channels:{server_id}")

        result = self.manager.get_channel(channel_id, user_id)
        assert result is not None
        return result

    def create_category(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
    ) -> ChannelCategory:
        """Create a new channel category."""
        name = name.strip()
        if not name:
            raise InvalidChannelNameError("Category name cannot be empty")

        pos_row = self.db.fetch_one(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos FROM srv_categories WHERE server_id = ?",
            (server_id,),
        )
        position = pos_row["next_pos"] if pos_row else 0

        now = self.manager._get_timestamp()
        cat_id = self.manager._generate_id()

        self.db.execute(
            """INSERT INTO srv_categories (id, server_id, name, position, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cat_id, server_id, name, position, now, now),
        )

        row = self.db.fetch_one("SELECT * FROM srv_categories WHERE id = ?", (cat_id,))
        return self.manager._row_to_category(row)

    def delete_category(self, user_id: SnowflakeID, category_id: SnowflakeID) -> bool:
        """Delete a channel category."""
        category_row = self.db.fetch_one(
            "SELECT * FROM srv_categories WHERE id = ?", (category_id,)
        )
        if not category_row:
            raise CategoryNotFoundError("Category not found")

        server_id = category_row["server_id"]
        self.db.execute(
            "UPDATE srv_channels SET category_id = NULL WHERE category_id = ?",
            (category_id,),
        )
        self.db.execute("DELETE FROM srv_categories WHERE id = ?", (category_id,))

        self.manager._log_audit(
            server_id, user_id, AuditLogAction.CHANNEL_DELETE, "category", category_id
        )
        return True

    def get_channels(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_type: Optional[ChannelType] = None,
    ) -> List[Channel]:
        """Get all channels in a server."""
        query = "SELECT * FROM srv_channels WHERE server_id = ? AND deleted = 0"
        params = [server_id]

        if channel_type:
            query += " AND channel_type = ?"
            params.append(channel_type.value)

        query += " ORDER BY position"
        rows = self.db.fetch_all(query, tuple(params))

        channels = []
        for row in rows:
            if self.manager.has_permission(user_id, server_id, "channels.view", row["id"]):
                channels.append(self.manager._row_to_channel(row))

        return channels

    def update_channel(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        name: Optional[str] = None,
        topic: Optional[str] = None,
        nsfw: Optional[bool] = None,
        slowmode_seconds: Optional[int] = None,
        category_id: Optional[SnowflakeID] = None,
    ) -> Channel:
        """Update channel settings."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        updates = []
        params = []
        changes = {}

        if name is not None:
            name = self.validate_channel_name(name, channel.channel_type)
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": channel.name, "new": name}

        if topic is not None:
            updates.append("topic = ?")
            params.append(topic)
            changes["topic"] = {"old": channel.topic, "new": topic}

        if nsfw is not None:
            updates.append("nsfw = ?")
            params.append(1 if nsfw else 0)
            changes["nsfw"] = {"old": channel.nsfw, "new": nsfw}

        if slowmode_seconds is not None:
            updates.append("slowmode_seconds = ?")
            params.append(slowmode_seconds)
            changes["slowmode_seconds"] = {
                "old": channel.slowmode_seconds,
                "new": slowmode_seconds,
            }

        if category_id is not None:
            if category_id != 0:
                cat = self.db.fetch_one(
                    "SELECT 1 FROM srv_categories WHERE id = ? AND server_id = ?",
                    (category_id, channel.server_id),
                )
                if not cat:
                    raise CategoryNotFoundError("Category not found")
            updates.append("category_id = ?")
            params.append(category_id if category_id != 0 else None)
            changes["category_id"] = {"old": channel.category_id, "new": category_id}

        if updates:
            updates.append("updated_at = ?")
            params.append(self.manager._get_timestamp())
            params.append(channel_id)

            self.db.execute(
                f"UPDATE srv_channels SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )

            self.manager._cache_invalidate(self.manager._channel_cache, channel_id)
            self.manager._log_audit(
                channel.server_id,
                user_id,
                AuditLogAction.CHANNEL_UPDATE,
                "channel",
                channel_id,
                changes,
            )

        result = self.manager.get_channel(channel_id, user_id)
        assert result is not None
        return result

    def delete_channel(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
        """Delete a channel."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        now = self.manager._get_timestamp()
        self.db.execute(
            "UPDATE srv_channels SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, channel_id),
        )

        self.manager._cache_invalidate(self.manager._channel_cache, channel_id)
        if redis_available():
            cache_delete(f"channel:{channel_id}")
            cache_delete(f"server_channels:{channel.server_id}")

        self.manager._log_audit(
            channel.server_id,
            user_id,
            AuditLogAction.CHANNEL_DELETE,
            "channel",
            channel_id,
        )
        return True

    def move_channel(
        self, user_id: SnowflakeID, channel_id: SnowflakeID, position: int
    ) -> Channel:
        """Move a channel to a new position."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")

        self.db.execute(
            "UPDATE srv_channels SET position = ?, updated_at = ? WHERE id = ?",
            (position, self.manager._get_timestamp(), channel_id),
        )

        self.manager._cache_invalidate(self.manager._channel_cache, channel_id)
        if redis_available():
            cache_delete(f"channel:{channel_id}")
            cache_delete(f"server_channels:{channel.server_id}")

        result = self.manager.get_channel(channel_id, user_id)
        assert result is not None
        return result
