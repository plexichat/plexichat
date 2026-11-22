from typing import Optional, List

from src.core.base import SnowflakeID
from src.core.events.types import EventType
from ..models import Notification, NotificationFeed
from ..exceptions import NotificationNotFoundError
from .helpers import row_to_notification


from .protocol import NotificationProtocol


class FeedMixin(NotificationProtocol):
    def get_notification(
        self,
        notification_id: SnowflakeID,
        user_id: Optional[SnowflakeID] = None,
    ) -> Optional[Notification]:
        """Fetch a notification by id, optionally scoped to its owner.

        ``user_id`` is ``None`` only for trusted internal callers that just
        inserted a row and need to read it back. Every caller handling a
        user-supplied notification id MUST pass ``user_id``: an unscoped
        lookup is an IDOR primitive even when the mutation that follows is
        guarded, because it hands another user's notification (including its
        decrypted content preview) to the caller.
        """
        if user_id is None:
            row = self._db.fetch_one(
                "SELECT * FROM notif_notifications WHERE id = ?", (notification_id,)
            )
        else:
            row = self._db.fetch_one(
                "SELECT * FROM notif_notifications WHERE id = ? AND user_id = ?",
                (notification_id, user_id),
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
        # Clamp from below too: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would bypass the page cap and dump the whole table.
        limit = max(0, min(limit, max_per_page))

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
        # Same lower clamp as get_notifications above: LIMIT -1 is unlimited.
        limit = max(0, min(limit, max_items))

        # Page items only — pagination-scoped ON PURPOSE so deeper pages
        # never shrink their item list.
        query = """
            SELECT
                id, user_id, sender_id, message_id, conversation_id, server_id, channel_id, thread_id,
                mention_type, content_preview, content_preview_encrypted, read, created_at
            FROM notif_notifications
            WHERE user_id = ?
        """
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

        # TOTAL/unread are USER-SCOPED, never page-scoped: computing them
        # via window functions inside the paginated SELECT made them change
        # depending on which page the caller requested (a ``before_id``
        # cursor filtered out older rows BEFORE the aggregate ran). A
        # client badge that reflects these numbers would undercount as soon
        # as the user scrolled past page one. Count across the user's full
        # notification set instead.
        count_row = self._db.fetch_one(
            """SELECT COUNT(*) AS total_count,
                      COALESCE(SUM(CASE WHEN read = 0 THEN 1 ELSE 0 END), 0) AS unread_count
               FROM notif_notifications WHERE user_id = ?""",
            (user_id,),
        )
        total_count = count_row["total_count"] if count_row else 0
        unread_count = count_row["unread_count"] if count_row else 0

        return NotificationFeed(
            notifications=notifications,
            total_count=total_count,
            unread_count=unread_count,
            has_more=has_more,
        )

    def mark_notification_read(
        self, user_id: SnowflakeID, notification_id: SnowflakeID
    ) -> bool:
        notif = self.get_notification(notification_id, user_id)
        if not notif:
            raise NotificationNotFoundError("Notification not found")

        # Idempotency (M5): the conditional UPDATE transitions the row only
        # when it is still unread, so a repeated mark-read is a no-op — it
        # must not double-decrement the mention/unread counters or emit a
        # second NOTIFICATION_UPDATE event for the same transition.
        # Ownership is enforced in the WRITE as well as the read above: the
        # fetch-then-check is a TOCTOU window, and a scoped UPDATE guarantees
        # no other user's row can ever be flipped even under a concurrent
        # reassignment. ``user_id`` is part of the predicate, not a post-hoc
        # comparison.
        cursor = self._db.execute(
            "UPDATE notif_notifications SET read = 1 "
            "WHERE id = ? AND user_id = ? AND read = 0",
            (notification_id, user_id),
        )
        if getattr(cursor, "rowcount", 0) == 0:
            # Already read (or concurrently marked): nothing transitioned.
            # Confirm the row still belongs to the caller before reporting
            # success — a concurrent delete between the scoped read and this
            # UPDATE must surface as "not found", not a false success for a
            # notification that no longer exists.
            if self.get_notification(notification_id, user_id) is None:
                raise NotificationNotFoundError("Notification not found")
            return True

        self._decrement_mention_count(user_id, notif.conversation_id)
        self._decrement_unread_count(user_id, notif.conversation_id)

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
            "UPDATE notif_unread SET mention_count = 0, unread_count = 0 WHERE user_id = ?",
            (user_id,),
        )

        self._dispatch_notification_event(
            user_id, EventType.NOTIFICATION_UPDATE, {"all_read": True}
        )

        return count

    def mark_channel_read(self, user_id: SnowflakeID, channel_id: SnowflakeID) -> int:
        # Capture per-conversation counts before marking as read. This grouped
        # read is the single source of truth: the returned count is the sum of
        # its per-conversation totals, so a separate COUNT(*) scan over the very
        # same filtered rows is redundant. One snapshot also keeps the count and
        # the counter decrements consistent.
        conv_rows = self._db.fetch_all(
            """SELECT conversation_id, COUNT(*) as total
               FROM notif_notifications WHERE user_id = ? AND channel_id = ? AND read = 0
               GROUP BY conversation_id""",
            (user_id, channel_id),
        )
        count = sum(row["total"] for row in conv_rows)

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE user_id = ? AND channel_id = ? AND read = 0",
            (user_id, channel_id),
        )

        # Decrement both counters for every affected conversation in one
        # bounded, owner-scoped pass. The old per-conversation loop issued two
        # SELECT + two UPDATE statements per conversation (4N queries); the
        # two counters share a row, so a single batched read/write suffices.
        self._decrement_unread_conversation_counts(
            user_id,
            {row["conversation_id"]: (row["total"], row["total"]) for row in conv_rows},
        )

        self._dispatch_notification_event(
            user_id,
            EventType.NOTIFICATION_UPDATE,
            {"channel_id": str(channel_id), "all_read": True},
        )

        return count

    def mark_server_read(self, user_id: SnowflakeID, server_id: SnowflakeID) -> int:
        # One grouped read feeds both the return value and the decrements (see
        # mark_channel_read); a standalone COUNT(*) over the same rows is gone.
        conv_rows = self._db.fetch_all(
            """SELECT conversation_id, COUNT(*) as total
               FROM notif_notifications WHERE user_id = ? AND server_id = ? AND read = 0
               GROUP BY conversation_id""",
            (user_id, server_id),
        )
        count = sum(row["total"] for row in conv_rows)

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE user_id = ? AND server_id = ? AND read = 0",
            (user_id, server_id),
        )

        # Batched, owner-scoped decrement of both counters (see
        # mark_channel_read): 2 queries per chunk instead of 4 per conversation.
        self._decrement_unread_conversation_counts(
            user_id,
            {row["conversation_id"]: (row["total"], row["total"]) for row in conv_rows},
        )

        self._dispatch_notification_event(
            user_id,
            EventType.NOTIFICATION_UPDATE,
            {"server_id": str(server_id), "all_read": True},
        )

        return count

    def mark_thread_read(self, user_id: SnowflakeID, thread_id: SnowflakeID) -> int:
        # One grouped read feeds both the return value and the decrements (see
        # mark_channel_read); a standalone COUNT(*) over the same rows is gone.
        conv_rows = self._db.fetch_all(
            """SELECT conversation_id, COUNT(*) as total
               FROM notif_notifications WHERE user_id = ? AND thread_id = ? AND read = 0
               GROUP BY conversation_id""",
            (user_id, thread_id),
        )
        count = sum(row["total"] for row in conv_rows)

        self._db.execute(
            "UPDATE notif_notifications SET read = 1 WHERE user_id = ? AND thread_id = ? AND read = 0",
            (user_id, thread_id),
        )

        # Batched, owner-scoped decrement of both counters (see
        # mark_channel_read).
        self._decrement_unread_conversation_counts(
            user_id,
            {row["conversation_id"]: (row["total"], row["total"]) for row in conv_rows},
        )

        self._dispatch_notification_event(
            user_id,
            EventType.NOTIFICATION_UPDATE,
            {"thread_id": str(thread_id), "all_read": True},
        )

        return count

    def delete_notification(
        self, user_id: SnowflakeID, notification_id: SnowflakeID
    ) -> bool:
        notif = self.get_notification(notification_id, user_id)
        if not notif:
            raise NotificationNotFoundError("Notification not found")

        # Owner-scoped, read-state-guarded delete. The decrement decision must
        # be made by the DELETE itself, not by ``notif.read`` from the fetch
        # above: that snapshot is stale the moment the SELECT commits. If a
        # concurrent mark-read flips the row to read=1 (and already
        # decremented) before this DELETE lands, a bare DELETE followed by
        # ``if not notif.read`` would decrement the SAME notification twice
        # and leave the badge too low. Gating the first DELETE on ``read = 0``
        # makes only the caller that actually transitions an unread row earn
        # the right to decrement. The predicate also carries the ownership
        # check, so a cross-user race cannot delete or decrement another
        # user's row.
        cursor = self._db.execute(
            "DELETE FROM notif_notifications WHERE id = ? AND user_id = ? AND read = 0",
            (notification_id, user_id),
        )
        if getattr(cursor, "rowcount", 0) > 0:
            self._decrement_mention_count(user_id, notif.conversation_id)
            self._decrement_unread_count(user_id, notif.conversation_id)
        else:
            # The row is either already read (a legitimate dismiss of a read
            # notification) or was deleted by a competing request. Delete it
            # without touching the counters -- ownership is still enforced in
            # the write, so a concurrent reassignment is a no-op here too.
            cursor = self._db.execute(
                "DELETE FROM notif_notifications WHERE id = ? AND user_id = ?",
                (notification_id, user_id),
            )
            if getattr(cursor, "rowcount", 0) == 0:
                raise NotificationNotFoundError("Notification not found")

        self._dispatch_notification_event(
            user_id,
            EventType.NOTIFICATION_DELETE,
            {"id": str(notification_id)},
        )

        return True
