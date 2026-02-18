"""
Notification manager - Core business logic for notification operations.

Handles mention parsing, notification creation, settings management,
and unread tracking with proper validation and permission checks.
"""

import asyncio
from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager, SnowflakeID
from src.core.events.types import EventType

from .models import (
    Mention,
    MentionType,
    Notification,
    NotificationSettings,
    ChannelNotificationOverride,
    NotificationLevel,
    UnreadCount,
    NotificationFeed,
    MentionPosition,
    PushPayload,
)
from .exceptions import (
    NotificationNotFoundError,
)
from .parser import parse_mentions as _parse_mentions
from src.core.database import cache_get, cache_set, cache_delete, redis_available


class NotificationManager(BaseManager):
    """Core notification manager handling all operations."""

    def __init__(
        self,
        db,
        auth_module=None,
        messaging_module=None,
        servers_module=None,
        relationships_module=None,
        presence_module=None,
    ):
        """
        Initialize the notification manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Auth module for user existence checks
            messaging_module: Messaging module for message access
            servers_module: Servers module for role/permission checks
            relationships_module: Relationships module for block filtering
            presence_module: Presence module for @here functionality
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._relationships = relationships_module
        self._presence = presence_module
        self._config = self._load_config()


        logger.info("Notification module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load notification configuration."""
        defaults = {
            "content_preview_length": 100,
            "max_notifications_per_page": 100,
            "max_feed_items": 100,
        }

        notif_config = config.get("notifications", {})
        return {**defaults, **notif_config}

    def _dispatch_notification_event(
        self, user_id: SnowflakeID, event_type: EventType, data: Dict[str, Any]
    ):
        """Dispatch a notification event via WebSocket."""
        try:
            from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
            from src.core.events.models import Event

            if ws_is_setup():
                dispatcher = get_dispatcher()

                async def dispatch():
                    try:
                        event = Event(
                            event_type=event_type,
                            data=data,
                        )
                        await dispatcher.dispatch_event(event, [user_id])
                    except Exception as e:
                        logger.debug(f"Failed to dispatch {event_type.name}: {e}")

                asyncio.create_task(dispatch())
        except Exception as e:
            logger.debug(f"Error preparing dispatch: {e}")

    def _get_message(self, message_id: SnowflakeID) -> Optional[Dict]:
        """Get message from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
        )

    def _get_conversation(self, conversation_id: SnowflakeID) -> Optional[Dict]:
        """Get conversation from database."""
        return self._db.fetch_one(
            "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
            (conversation_id,),
        )

    def _get_conversation_participants(self, conversation_id: int) -> List[int]:
        """Get all participant IDs in a conversation."""
        rows = self._db.fetch_all(
            "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
            (conversation_id,),
        )
        return [row["user_id"] for row in rows]

    def _is_blocked_by_either(self, user_id: int, other_id: int) -> bool:
        """Check if either user has blocked the other."""
        if not self._relationships:
            return False
        return self._relationships.is_blocked_by_either(user_id, other_id)

    def _get_blocked_user_ids(self, user_id: int) -> set:
        """Get IDs of users blocked by or blocking this user."""
        blocked = set()
        if self._relationships:
            blocked.update(self._relationships.get_all_blocked_ids(user_id))
            rows = self._db.fetch_all(
                "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
            )
            for row in rows:
                blocked.add(row["blocker_id"])
        return blocked

    def _truncate_content(self, content: str) -> str:
        """Truncate content for preview."""
        max_len = self._config.get("content_preview_length", 100)
        if len(content) <= max_len:
            return content
        return content[: max_len - 3] + "..."

    def _role_exists(self, role_id: SnowflakeID) -> bool:
        """Check if role exists."""
        row = self._db.fetch_one("SELECT 1 FROM srv_roles WHERE id = ?", (role_id,))
        return row is not None

    def _get_role(self, role_id: SnowflakeID) -> Optional[Dict]:
        """Get role from database."""
        return self._db.fetch_one("SELECT * FROM srv_roles WHERE id = ?", (role_id,))

    def _channel_exists(self, channel_id: SnowflakeID) -> bool:
        """Check if channel exists."""
        row = self._db.fetch_one(
            "SELECT 1 FROM srv_channels WHERE id = ?", (channel_id,)
        )
        return row is not None

    def _get_channel(self, channel_id: SnowflakeID) -> Optional[Dict]:
        """Get channel from database."""
        return self._db.fetch_one(
            "SELECT * FROM srv_channels WHERE id = ?", (channel_id,)
        )

    def _get_server_members(self, server_id: SnowflakeID) -> List[SnowflakeID]:
        """Get all member IDs in a server."""
        rows = self._db.fetch_all(
            "SELECT user_id FROM srv_members WHERE server_id = ?", (server_id,)
        )
        return [row["user_id"] for row in rows]

    def _get_role_members(self, role_id: SnowflakeID) -> List[SnowflakeID]:
        """Get all member IDs with a specific role."""
        rows = self._db.fetch_all(
            """SELECT m.user_id FROM srv_member_roles mr
               JOIN srv_members m ON mr.member_id = m.id
               WHERE mr.role_id = ?""",
            (role_id,),
        )
        return [row["user_id"] for row in rows]

    def _has_mention_everyone_permission(
        self,
        user_id: SnowflakeID,
        server_id: SnowflakeID,
        channel_id: Optional[SnowflakeID] = None,
    ) -> bool:
        """Check if user has permission to use @everyone/@here."""
        if not self._servers:
            return True
        return self._servers.has_permission(
            user_id, server_id, "messages.mention_everyone", channel_id
        )

    def _get_online_members(self, server_id: SnowflakeID) -> List[SnowflakeID]:
        """Get online member IDs in a server."""
        if not self._presence:
            return self._get_server_members(server_id)
        try:
            return self._presence.get_online_server_members(0, server_id)
        except Exception:
            return self._get_server_members(server_id)

    # === Mention Parsing ===

    def parse_mentions(self, content: str) -> List[Mention]:
        """Parse all mentions from message content."""
        return _parse_mentions(content)

    def validate_mentions(
        self,
        user_id: SnowflakeID,
        mentions: List[Mention],
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
    ) -> List[Mention]:
        """
        Validate mentions and mark invalid ones.

        Args:
            user_id: ID of user sending the message
            mentions: List of parsed mentions
            server_id: Optional server ID for permission checks
            channel_id: Optional channel ID for permission checks

        Returns:
            List of mentions with valid/invalid status updated
        """
        validated = []

        for mention in mentions:
            m = Mention(
                mention_type=mention.mention_type,
                target_id=mention.target_id,
                raw_text=mention.raw_text,
                start_pos=mention.start_pos,
                end_pos=mention.end_pos,
                valid=True,
            )

            if mention.mention_type == MentionType.USER:
                if mention.target_id is None or not self._user_exists(
                    mention.target_id
                ):
                    m.valid = False
                    m.error = "User not found"

            elif mention.mention_type == MentionType.ROLE:
                if mention.target_id is None or not self._role_exists(
                    mention.target_id
                ):
                    m.valid = False
                    m.error = "Role not found"
                elif server_id and mention.target_id is not None:
                    role = self._get_role(mention.target_id)
                    if role and role["server_id"] != server_id:
                        m.valid = False
                        m.error = "Role not in this server"
                    elif role and not bool(role["mentionable"]):
                        if not self._servers or not self._servers.has_permission(
                            user_id, server_id, "roles.manage", channel_id
                        ):
                            m.valid = False
                            m.error = "Role is not mentionable"

            elif mention.mention_type == MentionType.CHANNEL:
                if mention.target_id is None or not self._channel_exists(
                    mention.target_id
                ):
                    m.valid = False
                    m.error = "Channel not found"

            elif mention.mention_type in (MentionType.EVERYONE, MentionType.HERE):
                if server_id and channel_id is not None:
                    if not self._has_mention_everyone_permission(
                        user_id, server_id, channel_id
                    ):
                        m.valid = False
                        m.error = "No permission to mention everyone"
                elif server_id:
                    # No channel_id provided, check server-level permission
                    if not self._servers or not self._servers.has_permission(
                        user_id, server_id, "messages.mention_everyone"
                    ):
                        m.valid = False
                        m.error = "No permission to mention everyone"
                else:
                    m.valid = False
                    m.error = "Cannot use @everyone/@here in DMs"

            validated.append(m)

        return validated

    def highlight_mentions(
        self, content: str, user_id: SnowflakeID
    ) -> List[MentionPosition]:
        """
        Get positions of mentions relevant to a user for highlighting.

        Args:
            content: Message content
            user_id: ID of user viewing the message

        Returns:
            List of MentionPosition for highlighting
        """
        mentions = self.parse_mentions(content)
        positions = []

        user_roles = set()
        rows = self._db.fetch_all(
            """SELECT mr.role_id FROM srv_member_roles mr
               JOIN srv_members m ON mr.member_id = m.id
               WHERE m.user_id = ?""",
            (user_id,),
        )
        for row in rows:
            user_roles.add(row["role_id"])

        for mention in mentions:
            is_self = False

            if mention.mention_type == MentionType.USER:
                is_self = mention.target_id == user_id
            elif mention.mention_type == MentionType.ROLE:
                is_self = mention.target_id in user_roles
            elif mention.mention_type in (MentionType.EVERYONE, MentionType.HERE):
                is_self = True

            positions.append(
                MentionPosition(
                    start_pos=mention.start_pos,
                    end_pos=mention.end_pos,
                    mention_type=mention.mention_type,
                    is_self=is_self,
                )
            )

        return positions

    # === Notification Generation ===

    def create_notifications_for_message(
        self,
        message_id: SnowflakeID,
        author_id: Optional[SnowflakeID] = None,
        conversation_id: SnowflakeID = 0,
        content: str = "",
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        thread_id: Optional[SnowflakeID] = None,
        **kwargs,
    ) -> List[Notification]:
        """
        Create notifications for all mentioned users in a message.

        Args:
            message_id: ID of the message
            author_id: ID of message author
            conversation_id: ID of the conversation
            content: Message content
            server_id: Optional server ID
            channel_id: Optional channel ID
            thread_id: Optional thread ID
            **kwargs: Support for legacy 'sender_id'

        Returns:
            List of created notifications
        """
        actual_author_id = author_id or kwargs.get("sender_id")
        if actual_author_id is None:
            raise ValueError("author_id or sender_id must be provided")

        return self.create_notifications(
            message_id=message_id,
            author_id=actual_author_id,
            conversation_id=conversation_id,
            content=content,
            server_id=server_id,
            channel_id=channel_id,
            thread_id=thread_id,
        )

    def create_notifications(
        self,
        message_id: SnowflakeID,
        author_id: SnowflakeID,
        conversation_id: SnowflakeID,
        content: str,
        server_id: Optional[SnowflakeID] = None,
        channel_id: Optional[SnowflakeID] = None,
        thread_id: Optional[SnowflakeID] = None,
    ) -> List[Notification]:
        """
        Create notifications for all mentioned users in a message.

        Args:
            author_id: ID of message author
            message_id: ID of the message
            conversation_id: ID of the conversation
            content: Message content
            server_id: Optional server ID
            channel_id: Optional channel ID
            thread_id: Optional thread ID

        Returns:
            List of created notifications
        """
        mentions = self.parse_mentions(content)
        validated = self.validate_mentions(author_id, mentions, server_id, channel_id)

        users_to_notify = set()
        mention_types = {}

        blocked_users = self._get_blocked_user_ids(author_id)

        for mention in validated:
            if not mention.valid:
                continue

            if mention.mention_type == MentionType.USER:
                target_id = mention.target_id
                if target_id != author_id and target_id not in blocked_users:
                    users_to_notify.add(target_id)
                    mention_types[target_id] = MentionType.USER

            elif mention.mention_type == MentionType.ROLE:
                if mention.target_id is None:
                    continue
                role_members = self._get_role_members(mention.target_id)
                for member_id in role_members:
                    if member_id != author_id and member_id not in blocked_users:
                        if member_id not in mention_types:
                            users_to_notify.add(member_id)
                            mention_types[member_id] = MentionType.ROLE

            elif mention.mention_type == MentionType.EVERYONE:
                if server_id:
                    server_members = self._get_server_members(server_id)
                    for member_id in server_members:
                        if member_id != author_id and member_id not in blocked_users:
                            if member_id not in mention_types:
                                users_to_notify.add(member_id)
                                mention_types[member_id] = MentionType.EVERYONE

            elif mention.mention_type == MentionType.HERE:
                if server_id:
                    online_members = self._get_online_members(server_id)
                    for member_id in online_members:
                        if member_id != author_id and member_id not in blocked_users:
                            if member_id not in mention_types:
                                users_to_notify.add(member_id)
                                mention_types[member_id] = MentionType.HERE

        notifications = []
        now = self._get_timestamp()
        preview = self._truncate_content(content)

        for user_id in users_to_notify:
            mention_type = mention_types.get(user_id)
            if mention_type and self._should_notify_user(
                user_id, author_id, server_id, channel_id, mention_type
            ):
                notif = self._create_notification(
                    user_id=user_id,
                    author_id=author_id,
                    message_id=message_id,
                    conversation_id=conversation_id,
                    server_id=server_id,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    mention_type=mention_type,
                    content_preview=preview,
                    created_at=now,
                )
                if notif:
                    notifications.append(notif)
                    self._update_unread_count(
                        user_id, conversation_id, server_id, channel_id, is_mention=True
                    )

        return notifications

    def _should_notify_user(
        self,
        user_id: SnowflakeID,
        author_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        mention_type: MentionType,
    ) -> bool:
        """Check if user should receive notification based on settings."""
        # Suppress if user is currently focused on this channel/conversation
        if self._presence:
            try:
                presence = self._presence.get_presence(user_id)
                # Check for either specific channel focus or general conversation focus
                focused_id = presence.current_channel_id
                if focused_id and channel_id and int(focused_id) == int(channel_id):
                    logger.debug(
                        f"Suppressing notification for user {user_id} - already focused on channel {channel_id}"
                    )
                    return False
            except Exception as e:
                logger.debug(
                    f"Failed to check user focus during notification logic: {e}"
                )

        if channel_id:
            override = self.get_channel_override(user_id, channel_id)
            if override:
                now = self._get_timestamp()
                is_mute_active = (
                    override.muted_until is None or override.muted_until > now
                )
                if override.level == NotificationLevel.MUTED and is_mute_active:
                    return False
                if override.level == NotificationLevel.NOTHING:
                    return False

        settings = self.get_notification_settings(user_id, server_id)

        if settings.level == NotificationLevel.NOTHING:
            return False

        if mention_type in (MentionType.EVERYONE, MentionType.HERE):
            if settings.suppress_everyone:
                return False

        if mention_type == MentionType.ROLE:
            if settings.suppress_roles:
                return False

        return True

    def _create_notification(
        self,
        user_id: SnowflakeID,
        author_id: SnowflakeID,
        message_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        thread_id: Optional[SnowflakeID],
        mention_type: MentionType,
        content_preview: str,
        created_at: int,
    ) -> Optional[Notification]:
        """Create a single notification record."""
        notif_id = self._generate_id()

        self._db.execute(
            """INSERT INTO notif_notifications
               (id, user_id, sender_id, message_id, conversation_id, server_id, channel_id, thread_id,
                mention_type, content_preview, read, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                notif_id,
                user_id,
                author_id,
                message_id,
                conversation_id,
                server_id,
                channel_id,
                thread_id,
                mention_type.value,
                content_preview,
                created_at,
            ),
        )

        notification = self.get_notification(notif_id)
        if notification:
            # Import here to avoid circular imports in some environments
            from src.api.routes.notifications import _notif_to_response

            self._dispatch_notification_event(
                user_id,
                EventType.NOTIFICATION_CREATE,
                _notif_to_response(notification).model_dump(),
            )

        return notification

    # === Notification Operations ===

    def get_notification(self, notification_id: SnowflakeID) -> Optional[Notification]:
        """Get a notification by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM notif_notifications WHERE id = ?", (notification_id,)
        )
        if not row:
            return None
        return self._row_to_notification(row)

    def get_notifications(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        unread_only: bool = False,
    ) -> List[Notification]:
        """Get notifications for a user."""
        max_per_page = self._config.get("max_notifications_per_page", 100)
        limit = min(limit, max_per_page)

        query = "SELECT * FROM notif_notifications WHERE user_id = ?"
        params = [user_id]

        if unread_only:
            query += " AND read = 0"

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))
        return [self._row_to_notification(row) for row in rows]

    def mark_notification_read(
        self, user_id: SnowflakeID, notification_id: SnowflakeID
    ) -> bool:
        """Mark a notification as read."""
        notif = self.get_notification(notification_id)
        if not notif:
            raise NotificationNotFoundError("Notification not found")
        if notif.user_id != user_id:
            raise NotificationNotFoundError("Notification not found")

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE id = ?", (notification_id,)
        )

        self._decrement_mention_count(user_id, notif.conversation_id)

        self._dispatch_notification_event(
            user_id,
            EventType.NOTIFICATION_UPDATE,
            {"id": str(notification_id), "read": True},
        )

        return True

    def mark_all_read(self, user_id: SnowflakeID) -> int:
        """Mark all notifications as read for a user."""
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM notif_notifications WHERE user_id = ? AND read = 0",
            (user_id,),
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE user_id = ? AND read = 0",
            (user_id,),
        )

        self._db.execute(
            "UPDATE notif_unread SET mention_count = 0 WHERE user_id = ?", (user_id,)
        )

        self._dispatch_notification_event(
            user_id, EventType.NOTIFICATION_UPDATE, {"all_read": True}
        )

        return count

        return count

    def mark_channel_read(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> int:
        """Mark all notifications in a channel as read."""
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM notif_notifications WHERE user_id = ? AND channel_id = ? AND read = 0",
            (user_id, channel_id),
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE user_id = ? AND channel_id = ? AND read = 0",
            (user_id, channel_id),
        )

        self._db.execute(
            "UPDATE notif_unread SET mention_count = 0 WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        return count

    def mark_server_read(self, user_id: SnowflakeID, server_id: SnowflakeID) -> int:
        """Mark all notifications in a server as read."""
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM notif_notifications WHERE user_id = ? AND server_id = ? AND read = 0",
            (user_id, server_id),
        )
        count = count_row["count"] if count_row else 0

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE user_id = ? AND server_id = ? AND read = 0",
            (user_id, server_id),
        )

        self._db.execute(
            "UPDATE notif_unread SET mention_count = 0 WHERE user_id = ? AND server_id = ?",
            (user_id, server_id),
        )

        return count

    def delete_notification(
        self, user_id: SnowflakeID, notification_id: SnowflakeID
    ) -> bool:
        """Delete a notification."""
        notif = self.get_notification(notification_id)
        if not notif:
            raise NotificationNotFoundError("Notification not found")
        if notif.user_id != user_id:
            raise NotificationNotFoundError("Notification not found")

        if not notif.read:
            self._decrement_mention_count(user_id, notif.conversation_id)

        self._db.execute(
            "DELETE FROM notif_notifications WHERE id = ?", (notification_id,)
        )

        return True

    # === Unread Counts ===

    def _update_unread_count(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        is_mention: bool = False,
    ):
        """Update unread count for a user/conversation."""
        now = self._get_timestamp()

        existing = self._db.fetch_one(
            "SELECT id, unread_count, mention_count FROM notif_unread WHERE user_id = ? AND conversation_id = ?",
            (user_id, conversation_id),
        )

        if existing:
            new_unread = existing["unread_count"] + 1
            new_mention = existing["mention_count"] + (1 if is_mention else 0)
            self._db.execute(
                "UPDATE notif_unread SET unread_count = ?, mention_count = ?, updated_at = ? WHERE id = ?",
                (new_unread, new_mention, now, existing["id"]),
            )
        else:
            unread_id = self._generate_id()
            self._db.execute(
                """INSERT INTO notif_unread
                   (id, user_id, conversation_id, server_id, channel_id, unread_count, mention_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (
                    unread_id,
                    user_id,
                    conversation_id,
                    server_id,
                    channel_id,
                    1 if is_mention else 0,
                    now,
                ),
            )

    def _decrement_mention_count(
        self, user_id: SnowflakeID, conversation_id: SnowflakeID
    ):
        """Decrement mention count for a conversation."""
        self._db.execute(
            """UPDATE notif_unread SET mention_count = MAX(0, mention_count - 1)
               WHERE user_id = ? AND conversation_id = ?""",
            (user_id, conversation_id),
        )

    def get_unread_count(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> UnreadCount:
        """Get unread count for a user, optionally filtered by server."""
        if server_id:
            row = self._db.fetch_one(
                """SELECT COALESCE(SUM(unread_count), 0) as total, COALESCE(SUM(mention_count), 0) as mentions
                   FROM notif_unread WHERE user_id = ? AND server_id = ?""",
                (user_id, server_id),
            )
        else:
            row = self._db.fetch_one(
                """SELECT COALESCE(SUM(unread_count), 0) as total, COALESCE(SUM(mention_count), 0) as mentions
                   FROM notif_unread WHERE user_id = ?""",
                (user_id,),
            )

        return UnreadCount(
            user_id=user_id,
            conversation_id=0,  # Global unread count doesn't have a conversation ID
            total_unread=row["total"] if row else 0,
            mention_count=row["mentions"] if row else 0,
            server_id=server_id,
        )

    def get_unread_counts(self, user_id: SnowflakeID) -> Dict[SnowflakeID, UnreadCount]:
        """Get unread counts per server/conversation for a user."""
        rows = self._db.fetch_all(
            """SELECT conversation_id, server_id, channel_id, unread_count, mention_count
               FROM notif_unread WHERE user_id = ? AND (unread_count > 0 OR mention_count > 0)""",
            (user_id,),
        )

        counts = {}
        for row in rows:
            conv_id = row["conversation_id"]
            counts[conv_id] = UnreadCount(
                user_id=user_id,
                conversation_id=conv_id,
                total_unread=row["unread_count"],
                mention_count=row["mention_count"],
                server_id=row["server_id"],
                channel_id=row["channel_id"],
            )

        return counts

    def get_mention_count(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> int:
        """Get count of unread mentions for a user."""
        if server_id:
            row = self._db.fetch_one(
                "SELECT COALESCE(SUM(mention_count), 0) as count FROM notif_unread WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
        else:
            row = self._db.fetch_one(
                "SELECT COALESCE(SUM(mention_count), 0) as count FROM notif_unread WHERE user_id = ?",
                (user_id,),
            )

        return row["count"] if row else 0

    # === Notification Feed ===

    def get_notification_feed(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
    ) -> NotificationFeed:
        """Get recent mentions across all servers."""
        max_items = self._config.get("max_feed_items", 100)
        limit = min(limit, max_items)

        query = "SELECT * FROM notif_notifications WHERE user_id = ?"
        params = [user_id]

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit + 1)

        rows = self._db.fetch_all(query, tuple(params))

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        notifications = [self._row_to_notification(row) for row in rows]

        total_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM notif_notifications WHERE user_id = ?",
            (user_id,),
        )
        total_count = total_row["count"] if total_row else 0

        unread_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM notif_notifications WHERE user_id = ? AND read = 0",
            (user_id,),
        )
        unread_count = unread_row["count"] if unread_row else 0

        return NotificationFeed(
            notifications=notifications,
            total_count=total_count,
            unread_count=unread_count,
            has_more=has_more,
        )

    # === Settings Operations ===

    def get_notification_settings(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> NotificationSettings:
        """Get notification settings for a user (cached for 10 minutes)."""
        # Try cache first
        cache_key = f"notif_settings:{user_id}:{server_id or 'global'}"
        if redis_available():
            cached = cache_get(cache_key)
            if cached:
                return NotificationSettings(
                    user_id=cached["user_id"],
                    server_id=cached.get("server_id"),
                    level=NotificationLevel(cached["level"]),
                    dm_notifications=cached["dm_notifications"],
                    suppress_everyone=cached["suppress_everyone"],
                    suppress_roles=cached["suppress_roles"],
                    mobile_push=cached["mobile_push"],
                )

        # Cache miss - fetch from DB
        if server_id:
            row = self._db.fetch_one(
                "SELECT * FROM notif_settings WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
        else:
            row = self._db.fetch_one(
                "SELECT * FROM notif_settings WHERE user_id = ? AND server_id IS NULL",
                (user_id,),
            )

        if row:
            settings = self._row_to_settings(row)
        else:
            settings = NotificationSettings(
                user_id=user_id,
                server_id=server_id,
                level=NotificationLevel.ALL_MESSAGES,
                dm_notifications=True,
                suppress_everyone=False,
                suppress_roles=False,
                mobile_push=True,
            )

        # Cache the result (10 minute TTL)
        if redis_available():
            cache_set(
                cache_key,
                {
                    "user_id": settings.user_id,
                    "server_id": settings.server_id,
                    "level": settings.level.value,
                    "dm_notifications": settings.dm_notifications,
                    "suppress_everyone": settings.suppress_everyone,
                    "suppress_roles": settings.suppress_roles,
                    "mobile_push": settings.mobile_push,
                },
                ttl=600,
            )

        return settings

    def update_notification_settings(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None, **kwargs
    ) -> NotificationSettings:
        """Update notification settings for a user."""
        now = self._get_timestamp()

        if server_id:
            existing = self._db.fetch_one(
                "SELECT id FROM notif_settings WHERE user_id = ? AND server_id = ?",
                (user_id, server_id),
            )
        else:
            existing = self._db.fetch_one(
                "SELECT id FROM notif_settings WHERE user_id = ? AND server_id IS NULL",
                (user_id,),
            )

        level = kwargs.get("level", NotificationLevel.ALL_MESSAGES)
        if isinstance(level, str):
            level = NotificationLevel(level)

        dm_notifications = kwargs.get("dm_notifications", True)
        suppress_everyone = kwargs.get("suppress_everyone", False)
        suppress_roles = kwargs.get("suppress_roles", False)
        mobile_push = kwargs.get("mobile_push", True)

        if existing:
            self._db.execute(
                """UPDATE notif_settings SET
                   level = ?, dm_notifications = ?, suppress_everyone = ?,
                   suppress_roles = ?, mobile_push = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    level.value,
                    1 if dm_notifications else 0,
                    1 if suppress_everyone else 0,
                    1 if suppress_roles else 0,
                    1 if mobile_push else 0,
                    now,
                    existing["id"],
                ),
            )
        else:
            settings_id = self._generate_id()
            self._db.execute(
                """INSERT INTO notif_settings
                   (id, user_id, server_id, level, dm_notifications, suppress_everyone,
                    suppress_roles, mobile_push, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    settings_id,
                    user_id,
                    server_id,
                    level.value,
                    1 if dm_notifications else 0,
                    1 if suppress_everyone else 0,
                    1 if suppress_roles else 0,
                    1 if mobile_push else 0,
                    now,
                    now,
                ),
            )

        # Invalidate cache
        cache_key = f"notif_settings:{user_id}:{server_id or 'global'}"
        if redis_available():
            cache_delete(cache_key)

        return self.get_notification_settings(user_id, server_id)

    def get_channel_override(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> Optional[ChannelNotificationOverride]:
        """Get channel notification override for a user."""
        row = self._db.fetch_one(
            "SELECT * FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        if not row:
            return None

        return self._row_to_channel_override(row)

    def set_channel_override(
        self,
        user_id: SnowflakeID,
        channel_id: SnowflakeID,
        level: NotificationLevel,
        muted_until: Optional[int] = None,
    ) -> ChannelNotificationOverride:
        """Set channel notification override for a user."""
        now = self._get_timestamp()

        if isinstance(level, str):
            level = NotificationLevel(level)

        existing = self._db.fetch_one(
            "SELECT id FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        if existing:
            self._db.execute(
                """UPDATE notif_channel_overrides SET
                   level = ?, muted_until = ?, updated_at = ?
                   WHERE id = ?""",
                (level.value, muted_until, now, existing["id"]),
            )
        else:
            override_id = self._generate_id()
            self._db.execute(
                """INSERT INTO notif_channel_overrides
                   (id, user_id, channel_id, level, muted_until, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (override_id, user_id, channel_id, level.value, muted_until, now, now),
            )

        result = self.get_channel_override(user_id, channel_id)
        assert result is not None  # Should exist since we just created/updated it
        return result

    def delete_channel_override(
        self, user_id: SnowflakeID, channel_id: SnowflakeID
    ) -> bool:
        """Delete channel notification override."""
        existing = self._db.fetch_one(
            "SELECT 1 FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        if not existing:
            return False

        self._db.execute(
            "DELETE FROM notif_channel_overrides WHERE user_id = ? AND channel_id = ?",
            (user_id, channel_id),
        )

        return True

    # === Push Notification Hooks ===

    def prepare_push_payload(self, notification: Notification) -> PushPayload:
        """Prepare push notification payload (does not send)."""
        sender_name = "Someone"
        row = self._db.fetch_one(
            "SELECT username FROM auth_users WHERE id = ?", (notification.author_id,)
        )
        if row:
            sender_name = row["username"]

        if notification.mention_type == MentionType.USER:
            title = f"{sender_name} mentioned you"
        elif notification.mention_type == MentionType.ROLE:
            title = f"{sender_name} mentioned your role"
        elif notification.mention_type == MentionType.EVERYONE:
            title = f"{sender_name} mentioned @everyone"
        elif notification.mention_type == MentionType.HERE:
            title = f"{sender_name} mentioned @here"
        else:
            title = f"New mention from {sender_name}"

        unread = self.get_unread_count(notification.user_id)

        return PushPayload(
            user_id=notification.user_id,
            title=title,
            body=notification.content_preview,
            data={
                "notification_id": notification.id,
                "message_id": notification.message_id,
                "conversation_id": notification.conversation_id,
                "server_id": notification.server_id,
                "channel_id": notification.channel_id,
                "thread_id": notification.thread_id,
                "mention_type": notification.mention_type.value,
            },
            badge_count=unread.mention_count,
            sound="default",
            priority="high",
        )

    # === Row Converters ===

    def _row_to_notification(self, row) -> Notification:
        """Convert database row to Notification."""
        return Notification(
            id=row["id"],
            user_id=row["user_id"],
            author_id=row["sender_id"],
            message_id=row["message_id"],
            conversation_id=row["conversation_id"],
            server_id=row["server_id"],
            channel_id=row["channel_id"],
            thread_id=row.get("thread_id"),
            mention_type=MentionType(row["mention_type"]),
            content_preview=row["content_preview"],
            read=bool(row["read"]),
            created_at=row["created_at"],
        )

    def _row_to_settings(self, row) -> NotificationSettings:
        """Convert database row to NotificationSettings."""
        return NotificationSettings(
            user_id=row["user_id"],
            server_id=row["server_id"],
            level=NotificationLevel(row["level"]),
            dm_notifications=bool(row["dm_notifications"]),
            suppress_everyone=bool(row["suppress_everyone"]),
            suppress_roles=bool(row["suppress_roles"]),
            mobile_push=bool(row["mobile_push"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_channel_override(self, row) -> ChannelNotificationOverride:
        """Convert database row to ChannelNotificationOverride."""
        return ChannelNotificationOverride(
            user_id=row["user_id"],
            channel_id=row["channel_id"],
            level=NotificationLevel(row["level"]),
            muted_until=row["muted_until"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
