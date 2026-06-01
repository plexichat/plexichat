from typing import Optional, List, Dict, Any

import utils.logger as logger

from ..models import (
    Notification,
    NotificationSettings,
    ChannelNotificationOverride,
    MentionType,
    NotificationLevel,
)
from src.core.base import SnowflakeID


def row_to_notification(row: Dict[str, Any]) -> Notification:
    content_preview = row.get("content_preview") or ""
    if row.get("content_preview_encrypted"):
        try:
            from src.utils.encryption import decrypt_data

            content_preview = decrypt_data(
                row["content_preview_encrypted"],
                context=f"notification:{row['id']}",
            )
        except Exception as e:
            logger.warning(
                f"Failed to decrypt notification content_preview for {row['id']}: {e}"
            )
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
        content_preview=content_preview,
        read=bool(row["read"]),
        created_at=row["created_at"],
    )


def row_to_settings(row: Dict[str, Any]) -> NotificationSettings:
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


def row_to_channel_override(row: Dict[str, Any]) -> ChannelNotificationOverride:
    """Convert database row to ChannelNotificationOverride.

    Consolidates the two identical original methods _row_to_override
    and _row_to_channel_override into one.
    """
    return ChannelNotificationOverride(
        user_id=row["user_id"],
        channel_id=row["channel_id"],
        level=NotificationLevel(row["level"]),
        muted_until=row["muted_until"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_message(db, message_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one(
        "SELECT * FROM msg_messages WHERE id = ? AND deleted = 0", (message_id,)
    )


def get_conversation(db, conversation_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one(
        "SELECT * FROM msg_conversations WHERE id = ? AND deleted = 0",
        (conversation_id,),
    )


def get_conversation_participants(db, conversation_id: int) -> List[int]:
    rows = db.fetch_all(
        "SELECT user_id FROM msg_participants WHERE conversation_id = ?",
        (conversation_id,),
    )
    return [row["user_id"] for row in rows]


def is_blocked_by_either(relationships, user_id: int, other_id: int) -> bool:
    if not relationships:
        return False
    return relationships.is_blocked_by_either(user_id, other_id)


def get_blocked_user_ids(relationships, db, user_id: int) -> set:
    blocked = set()
    if relationships:
        blocked.update(relationships.get_all_blocked_ids(user_id))
        rows = db.fetch_all(
            "SELECT blocker_id FROM rel_blocked WHERE blocked_id = ?", (user_id,)
        )
        for row in rows:
            blocked.add(row["blocker_id"])
    return blocked


def truncate_content(content: str, config: Dict[str, Any]) -> str:
    max_len = config.get("content_preview_length", 100)
    if len(content) <= max_len:
        return content
    return content[: max_len - 3] + "..."


def role_exists(db, role_id: SnowflakeID) -> bool:
    row = db.fetch_one("SELECT 1 FROM srv_roles WHERE id = ?", (role_id,))
    return row is not None


def get_role(db, role_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one("SELECT * FROM srv_roles WHERE id = ?", (role_id,))


def channel_exists(db, channel_id: SnowflakeID) -> bool:
    row = db.fetch_one("SELECT 1 FROM srv_channels WHERE id = ?", (channel_id,))
    return row is not None


def get_channel(db, channel_id: SnowflakeID) -> Optional[Dict]:
    return db.fetch_one("SELECT * FROM srv_channels WHERE id = ?", (channel_id,))


def get_server_members(db, server_id: SnowflakeID) -> List[SnowflakeID]:
    rows = db.fetch_all(
        "SELECT user_id FROM srv_members WHERE server_id = ?", (server_id,)
    )
    return [row["user_id"] for row in rows]


def get_role_members(db, role_id: SnowflakeID) -> List[SnowflakeID]:
    rows = db.fetch_all(
        """SELECT m.user_id FROM srv_member_roles mr
           JOIN srv_members m ON mr.member_id = m.id
           WHERE mr.role_id = ?""",
        (role_id,),
    )
    return [row["user_id"] for row in rows]


def has_mention_everyone_permission(
    servers,
    user_id: SnowflakeID,
    server_id: SnowflakeID,
    channel_id: Optional[SnowflakeID] = None,
) -> bool:
    if not servers:
        return True
    return servers.has_permission(
        user_id, server_id, "messages.mention_everyone", channel_id
    )


def get_online_members(presence, db, server_id: SnowflakeID) -> List[SnowflakeID]:
    if not presence:
        return get_server_members(db, server_id)
    try:
        return presence.get_online_server_members(0, server_id)
    except Exception:
        return get_server_members(db, server_id)
