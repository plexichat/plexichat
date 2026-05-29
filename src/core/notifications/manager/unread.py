from typing import Optional, Dict

from src.core.base import SnowflakeID
from ..models import UnreadCount


from .protocol import NotificationProtocol


class UnreadMixin(NotificationProtocol):
    def _update_unread_count(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        server_id: Optional[SnowflakeID],
        channel_id: Optional[SnowflakeID],
        is_mention: bool = False,
    ):
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
        self._db.execute(
            """UPDATE notif_unread SET mention_count = MAX(0, mention_count - 1)
               WHERE user_id = ? AND conversation_id = ?""",
            (user_id, conversation_id),
        )

    def get_unread_count(
        self, user_id: SnowflakeID, server_id: Optional[SnowflakeID] = None
    ) -> UnreadCount:
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
            conversation_id=0,
            total_unread=row["total"] if row else 0,
            mention_count=row["mentions"] if row else 0,
            server_id=server_id,
        )

    def get_unread_counts(self, user_id: SnowflakeID) -> Dict[SnowflakeID, UnreadCount]:
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
