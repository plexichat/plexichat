from typing import Optional, List

from src.core.base import SnowflakeID
from src.core.events.types import EventType
from ..models import Notification, NotificationFeed
from ..exceptions import NotificationNotFoundError
from .helpers import row_to_notification


from .protocol import NotificationProtocol


class FeedMixin(NotificationProtocol):
    def get_notification(self, notification_id: SnowflakeID) -> Optional[Notification]:
        row = self._db.fetch_one(
            "SELECT * FROM notif_notifications WHERE id = ?", (notification_id,)
        )
        if not row:
            return None
        return row_to_notification(row)

    def get_notifications(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
        unread_only: bool = False,
    ) -> List[Notification]:
        max_per_page = self._config.get("max_notifications_per_page", 100)
        limit = min(limit, max_per_page)

        cols = "id, user_id, sender_id, message_id, conversation_id, server_id, channel_id, thread_id, mention_type, content_preview, content_preview_encrypted, read, created_at"
        query = f"SELECT {cols} FROM notif_notifications WHERE user_id = ?"
        params = [user_id]

        if unread_only:
            query += " AND read = 0"

        if before_id:
            query += " AND id < ?"
            params.append(before_id)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))
        return [row_to_notification(row) for row in rows]

    def get_notification_feed(
        self,
        user_id: SnowflakeID,
        limit: int = 50,
        before_id: Optional[SnowflakeID] = None,
    ) -> NotificationFeed:
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

        notifications = [row_to_notification(row) for row in rows]

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

    def mark_notification_read(
        self, user_id: SnowflakeID, notification_id: SnowflakeID
    ) -> bool:
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

    def mark_channel_read(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> int:
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
