"""
Channel and category handler for server operations.
"""

import re
from typing import Optional, List, Union, Any
import utils.logger as logger
from src.core.base import SnowflakeID
from ..models import Channel, ChannelCategory, ChannelType, AuditLogAction
from ..exceptions import (
    ChannelNotFoundError,
    CategoryNotFoundError,
    InvalidChannelNameError,
    ServerAccessDeniedError,
    ServerNotFoundError,
)
from ..permissions import has_permission as check_permission
from ..manager.converters import _row_to_channel, _row_to_category
from src.core.database import cache_delete, redis_available
from src.core.database.cache import cached, invalidate_pattern


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
                raise InvalidChannelNameError(
                    "Channel name must contain alphanumeric characters"
                )
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
        read_receipts_enabled: bool = True,
    ) -> Optional[Channel]:
        """Create a new channel in a server."""
        server = self.manager.get_server(server_id, user_id)
        if not server:
            raise ServerNotFoundError("Server not found")

        self.manager.require_permission(user_id, server_id, "channels.manage")

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

        # Store topic in topic_encrypted column (or topic column for backward compat)
        # If encryption is enabled, encrypt it; otherwise, store as plaintext
        topic_encrypted = None
        if topic:
            if self.manager._encrypt_descriptions:
                from src.utils.encryption import encrypt_data

                topic_encrypted = encrypt_data(topic)
            else:
                topic_encrypted = topic

        # After migration 029, unencrypted topic column no longer exists.
        # Always use topic_encrypted (encrypted or plaintext based on config).
        self.db.execute(
            """INSERT INTO srv_channels 
               (id, server_id, name, channel_type, category_id, position, topic_encrypted, nsfw, slowmode_seconds, read_receipts_enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                channel_id,
                server_id,
                name,
                channel_type.value,
                category_id,
                position,
                topic_encrypted,
                1 if nsfw else 0,
                slowmode_seconds,
                1 if read_receipts_enabled else 0,
                now,
                now,
            ),
        )

        if (
            channel_type in (ChannelType.TEXT, ChannelType.ANNOUNCEMENT)
            and self.manager._messaging
        ):
            conv = None
            try:
                conv = (
                    self.manager._messaging.create_server_channel_conversation(
                        server_id, channel_id
                    )
                    if hasattr(
                        self.manager._messaging, "create_server_channel_conversation"
                    )
                    else None
                )
                if conv:
                    self.db.execute(
                        "UPDATE srv_channels SET conversation_id = ? WHERE id = ?",
                        (conv.id, channel_id),
                    )
            except Exception:
                if conv and hasattr(self.manager._messaging, "delete_conversation"):
                    try:
                        self.manager._messaging.delete_conversation(conv.id)
                    except Exception as cleanup_error:
                        logger.debug(
                            f"Failed to roll back conversation {getattr(conv, 'id', None)}: {cleanup_error}"
                        )
                self.db.execute("DELETE FROM srv_channels WHERE id = ?", (channel_id,))
                raise

        self.manager._log_audit(
            server_id,
            user_id,
            AuditLogAction.CHANNEL_CREATE,
            "channel",
            channel_id,
        )

        if redis_available():
            cache_delete(f"server_channels:{server_id}")
        invalidate_pattern(f"server_channels:*{server_id}*")

        result = self.manager.get_channel(channel_id, user_id)
        if result:
            return result
        # Fallback: direct DB fetch if membership/permission check filtered it out
        row = self.db.fetch_one(
            "SELECT * FROM srv_channels WHERE id = ? AND deleted = 0", (channel_id,)
        )
        if row:
            return _row_to_channel(row, self.manager._encrypt_descriptions)
        return None

    def create_category(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        name: str,
    ) -> ChannelCategory:
        """Create a new channel category."""
        self.manager.require_permission(user_id, server_id, "channels.manage")
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
        return _row_to_category(row)

    def delete_category(self, user_id: SnowflakeID, category_id: SnowflakeID) -> bool:
        """Delete a channel category."""
        category_row = self.db.fetch_one(
            "SELECT * FROM srv_categories WHERE id = ?", (category_id,)
        )
        if not category_row:
            raise CategoryNotFoundError("Category not found")

        server_id = category_row["server_id"]
        self.manager.require_permission(user_id, server_id, "channels.manage")
        self.db.execute(
            "UPDATE srv_channels SET category_id = NULL WHERE category_id = ?",
            (category_id,),
        )
        self.db.execute("DELETE FROM srv_categories WHERE id = ?", (category_id,))

        self.manager._log_audit(
            server_id, user_id, AuditLogAction.CHANNEL_DELETE, "category", category_id
        )
        return True

    @cached(ttl=30, prefix="server_channels")
    def get_channels(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_type: Optional[ChannelType] = None,
    ) -> List[Channel]:
        """Get all channels in a server."""
        if not self.manager._is_member(server_id, user_id):
            raise ServerAccessDeniedError("Not a member of this server")
        query = "SELECT * FROM srv_channels WHERE server_id = ? AND deleted = 0"
        params: List[Any] = [server_id]

        if channel_type:
            query += " AND channel_type = ?"
            params.append(channel_type.value)

        query += " ORDER BY position"
        rows = self.db.fetch_all(query, tuple(params))

        if not rows:
            return []

        # Optimization: Batch permission check
        channel_ids = [row["id"] for row in rows]
        perms_map = self.manager.role_handler.get_permissions_batch(
            user_id, server_id, channel_ids
        )

        channels = []
        for row in rows:
            cid = row["id"]
            perms = perms_map.get(cid, {})
            # If batch fails or returns empty for some reason, we default to denied (safe)
            # unless administrator (handled in get_permissions_batch)
            if check_permission(perms, "channels.view"):
                channels.append(
                    _row_to_channel(row, self.manager._encrypt_descriptions)
                )

        return channels

    def update_channel(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        name: Optional[str] = None,
        topic: Optional[str] = None,
        nsfw: Optional[bool] = None,
        slowmode_seconds: Optional[int] = None,
        read_receipts_enabled: Optional[bool] = None,
        category_id: Optional[SnowflakeID] = None,
    ) -> Channel:
        """Update channel settings."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")
        self.manager.require_permission(
            user_id, channel.server_id, "channels.manage", channel_id
        )

        updates = []
        params = []
        changes = {}

        if name is not None:
            name = self.validate_channel_name(name, channel.channel_type)
            updates.append("name = ?")
            params.append(name)
            changes["name"] = {"old": channel.name, "new": name}

        if topic is not None:
            # After migration 029, unencrypted topic column no longer exists.
            # Always use topic_encrypted (encrypted or plaintext based on config).
            topic_encrypted = None
            if topic:
                if self.manager._encrypt_descriptions:
                    from src.utils.encryption import encrypt_data

                    topic_encrypted = encrypt_data(topic)
                else:
                    topic_encrypted = topic

            updates.append("topic_encrypted = ?")
            params.append(topic_encrypted)
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

        if read_receipts_enabled is not None:
            updates.append("read_receipts_enabled = ?")
            params.append(1 if read_receipts_enabled else 0)
            changes["read_receipts_enabled"] = {
                "old": channel.read_receipts_enabled,
                "new": read_receipts_enabled,
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
            # Avoid dynamic UPDATE to satisfy bandit - execute individual updates per column
            now = self.manager._get_timestamp()
            for i, update in enumerate(updates):
                col_name = update.split(" = ")[0]
                value = params[i]
                query = (
                    "UPDATE srv_channels SET "  # nosec: B608
                    + col_name
                    + " = ?, updated_at = ? WHERE id = ?"  # nosec: B608
                )
                self.db.execute(query, (value, now, channel_id))

            self.manager._cache_invalidate(
                self.manager._channel_cache_prefix, channel_id
            )
            self.manager._log_audit(
                channel.server_id,
                user_id,
                AuditLogAction.CHANNEL_UPDATE,
                "channel",
                channel_id,
                changes,
            )

        invalidate_pattern(f"server_channels:*{channel.server_id}*")
        result = self.manager.get_channel(channel_id, user_id)
        assert result is not None
        return result

    def delete_channel(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> bool:
        """Delete a channel."""
        channel = self.manager.get_channel(channel_id, user_id)
        if not channel:
            raise ChannelNotFoundError("Channel not found")
        self.manager.require_permission(
            user_id, channel.server_id, "channels.manage", channel_id
        )

        now = self.manager._get_timestamp()
        self.db.execute(
            "UPDATE srv_channels SET deleted = 1, deleted_at = ? WHERE id = ?",
            (now, channel_id),
        )

        self.manager._cache_invalidate(self.manager._channel_cache_prefix, channel_id)
        if redis_available():
            cache_delete(f"channel:{channel_id}")
            cache_delete(f"server_channels:{channel.server_id}")
        invalidate_pattern(f"server_channels:*{channel.server_id}*")

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
        self.manager.require_permission(
            user_id, channel.server_id, "channels.manage", channel_id
        )

        self.db.execute(
            "UPDATE srv_channels SET position = ?, updated_at = ? WHERE id = ?",
            (position, self.manager._get_timestamp(), channel_id),
        )

        self.manager._cache_invalidate(self.manager._channel_cache_prefix, channel_id)
        if redis_available():
            cache_delete(f"channel:{channel_id}")
            cache_delete(f"server_channels:{channel.server_id}")

        result = self.manager.get_channel(channel_id, user_id)
        assert result is not None
        return result
