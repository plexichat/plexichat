from typing import Any, Dict, Optional, List

from ..models import Thread, ThreadType, ThreadState
from ..exceptions import (
    ChannelNotFoundError,
)

from .helpers import (
    _get_channel,
    _get_thread_internal,
    _row_to_thread,
    _check_auto_archive,
)
from .protocol import ThreadProtocol


class ThreadListingMixin(ThreadProtocol):
    def _channel_view_allowed(
        self, user_id: int, channel: Dict[str, Any], channel_id: int
    ) -> bool:
        """Evaluate ``channels.view`` for a channel, independent of its contents.

        SECURITY: the channel listing used to derive this from the threads it
        had already fetched, so a channel with no matching threads returned an
        empty list *before* the permission was ever evaluated -- the check was
        skipped rather than answered. The permission is a property of the
        caller and the channel, not of the rows, so it is resolved here, up
        front, and fails closed when the channel has no owning server or the
        servers module is not wired (``_check_permission``).
        """
        server_id = channel.get("server_id")
        if server_id is None:
            return False
        return self._check_permission(user_id, server_id, "channels.view", channel_id)

    def get_thread(self, user_id: int, thread_id: int) -> Optional[Thread]:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            return None

        if not self.can_view_thread(user_id, thread_id):
            return None

        now = self._get_timestamp()
        if _check_auto_archive(thread, now):
            self._db.execute(
                "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
                (ThreadState.ARCHIVED.value, now, thread_id),
            )
            return _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)

        return thread

    def get_active_threads(self, user_id: int, channel_id: int) -> List[Thread]:
        channel = _get_channel(self._db, channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        has_server_view = self._channel_view_allowed(user_id, channel, channel_id)

        rows = self._db.fetch_all(
            """SELECT * FROM thread_threads 
               WHERE channel_id = ? AND state = ? AND deleted = 0
               ORDER BY last_message_at DESC NULLS LAST""",
            (channel_id, ThreadState.ACTIVE.value),
        )

        threads = []
        possible_threads = [
            _row_to_thread(row, self._encrypt_thread_names) for row in rows
        ]

        now = self._get_timestamp()
        active_threads = []
        for thread in possible_threads:
            if _check_auto_archive(thread, now):
                self._db.execute(
                    "UPDATE thread_threads SET state = ?, archived_at = ? WHERE id = ?",
                    (ThreadState.ARCHIVED.value, now, thread.id),
                )
                continue
            active_threads.append(thread)

        if not active_threads:
            return []

        private_thread_ids = {
            t.id for t in active_threads if t.thread_type == ThreadType.PRIVATE
        }
        member_thread_ids = set()
        if private_thread_ids:
            if not all(isinstance(tid, int) for tid in private_thread_ids):
                raise ValueError("All thread IDs must be integers")
            placeholders = ",".join("?" * len(private_thread_ids))
            rows = self._db.fetch_all(
                f"""SELECT thread_id FROM thread_members
                    WHERE user_id = ? AND thread_id IN ({placeholders})""",
                (user_id, *private_thread_ids),
            )
            member_thread_ids = {r["thread_id"] for r in rows}

        # SECURITY: ``has_server_view`` was resolved above from the channel
        # itself, so an empty listing cannot bypass the check. Public threads
        # are revealed only by that positive result.
        for thread in active_threads:
            if thread.thread_type == ThreadType.PRIVATE:
                if thread.id in member_thread_ids:
                    threads.append(thread)
            elif has_server_view:
                threads.append(thread)

        return threads

    def get_archived_threads(
        self,
        user_id: int,
        channel_id: int,
        limit: int = 50,
        before_timestamp: Optional[int] = None,
    ) -> List[Thread]:
        channel = _get_channel(self._db, channel_id)
        if not channel:
            raise ChannelNotFoundError(f"Channel {channel_id} not found")

        has_server_view = self._channel_view_allowed(user_id, channel, channel_id)

        if before_timestamp:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_threads 
                   WHERE channel_id = ? AND state = ? AND deleted = 0 AND archived_at < ?
                   ORDER BY archived_at DESC, id DESC LIMIT ?""",
                (channel_id, ThreadState.ARCHIVED.value, before_timestamp, limit),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM thread_threads 
                   WHERE channel_id = ? AND state = ? AND deleted = 0
                   ORDER BY archived_at DESC, id DESC LIMIT ?""",
                (channel_id, ThreadState.ARCHIVED.value, limit),
            )

        archived_threads = [
            _row_to_thread(row, self._encrypt_thread_names) for row in rows
        ]
        if not archived_threads:
            return []

        private_thread_ids = {
            t.id for t in archived_threads if t.thread_type == ThreadType.PRIVATE
        }
        member_thread_ids = set()
        if private_thread_ids:
            if not all(isinstance(tid, int) for tid in private_thread_ids):
                raise ValueError("All thread IDs must be integers")
            placeholders = ",".join("?" * len(private_thread_ids))
            rows = self._db.fetch_all(
                f"""SELECT thread_id FROM thread_members
                    WHERE user_id = ? AND thread_id IN ({placeholders})""",
                (user_id, *private_thread_ids),
            )
            member_thread_ids = {r["thread_id"] for r in rows}

        # SECURITY: same up-front, contents-independent check as
        # get_active_threads (and the same fail-closed default).
        threads = []
        for thread in archived_threads:
            if thread.thread_type == ThreadType.PRIVATE:
                if thread.id in member_thread_ids:
                    threads.append(thread)
            elif has_server_view:
                threads.append(thread)

        return threads

    def get_user_threads(
        self, user_id: int, include_archived: bool = False
    ) -> List[Thread]:
        if include_archived:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id,),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.state = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadState.ACTIVE.value),
            )

        return self._filter_user_threads(rows, user_id)

    def get_user_private_threads(
        self, user_id: int, channel_id: Optional[int] = None
    ) -> List[Thread]:
        if channel_id:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.thread_type = ? AND t.channel_id = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadType.PRIVATE.value, channel_id),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT t.* FROM thread_threads t
                   JOIN thread_members m ON t.id = m.thread_id
                   WHERE m.user_id = ? AND t.thread_type = ? AND t.deleted = 0
                   ORDER BY t.last_message_at DESC NULLS LAST""",
                (user_id, ThreadType.PRIVATE.value),
            )

        return self._filter_user_threads(rows, user_id)

    def _filter_user_threads(self, rows: List[Any], user_id: int) -> List[Thread]:
        """Re-check channel/server access for every listed thread.

        Membership alone is not proof of visibility: a user's channel or
        server access can be revoked after they joined a public thread.
        Private threads stay membership-gated by definition; public ones
        must still pass ``can_view_thread`` (which fails closed without a
        servers module).
        """
        visible: List[Thread] = []
        for row in rows:
            thread = _row_to_thread(row, self._encrypt_thread_names)
            if thread.thread_type == ThreadType.PRIVATE:
                visible.append(thread)
            elif self.can_view_thread(user_id, thread.id):
                visible.append(thread)
        return visible
