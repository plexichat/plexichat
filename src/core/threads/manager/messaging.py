from typing import Optional, List, Dict, Any

import utils.logger as logger
from ..models import ThreadState
from ..exceptions import (
    ThreadError,
    ThreadNotFoundError,
    ThreadAccessDeniedError,
    ThreadArchivedError,
    ThreadLockedError,
)

from .helpers import _get_thread_internal
from .protocol import ThreadProtocol


class ThreadMessagingMixin(ThreadProtocol):
    def _enforce_slowmode(self, user_id: int, thread_id: int, can_manage: bool) -> None:
        """Enforce thread slowmode before sending a message (no-op if disabled)."""
        from src.core.threads.slowmode import ThreadSlowmode

        result = ThreadSlowmode(self._db).check_slowmode(
            thread_id, user_id, is_moderator=can_manage
        )
        if result.get("allowed"):
            return

        from src.core.ratelimit.exceptions import RateLimitError

        retry_after_ms = result.get("retry_after_ms", 0)
        raise RateLimitError(
            f"Slowmode is enabled. Try again in {retry_after_ms / 1000:.1f}s",
            retry_after_ms / 1000.0,
        )

    def _record_message_sent(self, user_id: int, thread_id: int) -> None:
        """Record a successful send for slowmode tracking (best-effort)."""
        try:
            from src.core.threads.slowmode import ThreadSlowmode

            ThreadSlowmode(self._db).record_message_sent(thread_id, user_id)
        except Exception as e:
            logger.debug(f"Slowmode tracking skipped for thread {thread_id}: {e}")

    def send_message(
        self,
        user_id: int,
        thread_id: int,
        content: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        can_manage = self.can_manage_thread(user_id, thread_id)

        if thread.locked:
            if not can_manage:
                raise ThreadLockedError("Thread is locked")

        if not self.can_send_in_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot send messages in this thread")

        if thread.state == ThreadState.ARCHIVED:
            raise ThreadArchivedError("Thread is archived")

        if not self._is_member(user_id, thread_id):
            self.join_thread(user_id, thread_id)

        if not self._messaging or not thread.conversation_id:
            raise ThreadError(
                "Cannot send message: thread has no messaging conversation"
            )

        self._enforce_slowmode(user_id, thread_id, can_manage)

        # TOCTOU guard (M5): re-read state immediately before writing — an
        # archive may have committed after the checks above, and the stale
        # snapshot must not let a message land in an archived thread.
        fresh = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if fresh is None:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")
        if fresh.state == ThreadState.ARCHIVED:
            raise ThreadArchivedError("Thread is archived")

        now = self._get_timestamp()

        msg = self._messaging.send_message(
            user_id=user_id,
            conversation_id=thread.conversation_id,
            content=content,
            attachments=attachments,
        )
        message_id = msg.id

        self._db.execute(
            """INSERT INTO thread_messages (id, thread_id, message_id, user_id, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (self._generate_id(), thread_id, message_id, user_id, now),
        )

        self._db.execute(
            """UPDATE thread_threads 
               SET message_count = message_count + 1, last_message_at = ?
               WHERE id = ?""",
            (now, thread_id),
        )

        self._record_message_sent(user_id, thread_id)

        logger.debug(f"Message sent to thread {thread_id} by user {user_id}")

        return {
            "id": message_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "content": content,
            "created_at": now,
        }

    def remove_message(self, user_id: int, thread_id: int, message_id: int) -> bool:
        """Remove a message reference from a thread, keeping counters accurate.

        Deletes the ``thread_messages`` row for the message and decrements the
        thread's ``message_count`` (never below zero).

        Only the sender of the link, or a thread manager, may remove it:
        mere viewers get :class:`ThreadAccessDeniedError`.
        """
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        row = self._db.fetch_one(
            "SELECT id, user_id FROM thread_messages WHERE thread_id = ? AND message_id = ?",
            (thread_id, message_id),
        )
        if not row:
            return False

        if int(row["user_id"]) != int(user_id) and not self.can_manage_thread(
            user_id, thread_id
        ):
            raise ThreadAccessDeniedError(
                "Only the sender or a thread manager can remove this message"
            )

        self._db.execute(
            "DELETE FROM thread_messages WHERE id = ?",
            (row["id"],),
        )

        self._db.execute(
            """UPDATE thread_threads
               SET message_count = MAX(message_count - 1, 0)
               WHERE id = ?""",
            (thread_id,),
        )

        self._db.execute(
            """UPDATE thread_threads
               SET last_message_at = (
                   SELECT MAX(tm.created_at) FROM thread_messages tm
                   WHERE tm.thread_id = thread_threads.id
               )
               WHERE id = ?""",
            (thread_id,),
        )

        logger.debug(f"Message {message_id} removed from thread {thread_id}")

        return True

    def get_messages(
        self,
        user_id: int,
        thread_id: int,
        limit: int = 50,
        before_id: Optional[int] = None,
        after_id: Optional[int] = None,
    ) -> List[Any]:
        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        # Clamp from below: SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit would dump the whole thread history. (No upper cap
        # here on purpose — pagination callers already bound page sizes.)
        limit = max(0, limit)

        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        conversation_id = thread.conversation_id if thread else None

        # SECURITY (column projection): select ONLY the safe, response
        # columns. ``SELECT tm.*`` previously leaked internal storage
        # fields (content_encrypted ciphertext, message_created_at) into
        # every payload returned to clients.
        query = """
            SELECT tm.id, tm.thread_id, tm.message_id, tm.user_id,
                   m.author_id, m.content, tm.created_at
            FROM thread_messages tm
            JOIN msg_messages m ON tm.message_id = m.id
            WHERE tm.thread_id = ? AND m.deleted = 0
        """
        params: List[Any] = [thread_id]

        if before_id:
            query += " AND tm.message_id < ?"
            params.append(before_id)
            query += " ORDER BY tm.message_id DESC LIMIT ?"
        elif after_id:
            query += " AND tm.message_id > ?"
            params.append(after_id)
            query += " ORDER BY tm.message_id ASC LIMIT ?"
        else:
            query += " ORDER BY tm.message_id DESC LIMIT ?"

        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))

        from src.utils.encryption import decrypt_message

        results = []
        for row in rows:
            data = dict(row)
            content = data.get("content", "")

            try:
                data["content"] = decrypt_message(
                    content,
                    data["message_id"],
                    conversation_id=conversation_id,
                )
            except Exception as e:
                logger.debug(
                    f"Failed to decrypt message {data['message_id']} in thread {thread_id}: {e}"
                )

            results.append(data)

        return results

    def unlink_deleted_message(self, message_id: int) -> int:
        """Remove every thread link for a deleted messaging message.

        Deletes all ``thread_messages`` rows referencing ``message_id``,
        decrements each affected thread's ``message_count`` (floor 0),
        recomputes ``last_message_at`` and clears the
        ``parent_message_id`` starter linkage when the deleted message
        started a thread.

        Returns:
            Number of thread links removed.
        """
        links = self._db.fetch_all(
            "SELECT id, thread_id FROM thread_messages WHERE message_id = ?",
            (message_id,),
        )

        removed = 0
        affected_thread_ids: List[int] = []
        for link in links:
            self._db.execute(
                "DELETE FROM thread_messages WHERE id = ?",
                (link["id"],),
            )
            removed += 1
            if int(link["thread_id"]) not in affected_thread_ids:
                affected_thread_ids.append(int(link["thread_id"]))

        for thread_id in affected_thread_ids:
            self._db.execute(
                """UPDATE thread_threads
                   SET message_count = MAX(message_count - 1, 0)
                   WHERE id = ?""",
                (thread_id,),
            )
            self._db.execute(
                """UPDATE thread_threads
                   SET last_message_at = (
                       SELECT MAX(tm.created_at) FROM thread_messages tm
                       WHERE tm.thread_id = thread_threads.id
                   )
                   WHERE id = ?""",
                (thread_id,),
            )

        # Clear starter linkage EVEN when the deleted message was never
        # linked into thread_messages: a thread's starter message is
        # referenced only via parent_message_id.
        self._db.execute(
            "UPDATE thread_threads SET parent_message_id = NULL "
            "WHERE parent_message_id = ? AND deleted = 0",
            (message_id,),
        )

        logger.debug(
            f"Unlinked message {message_id} from {removed} thread(s) "
            f"({len(affected_thread_ids)} thread(s) re-counted)"
        )
        return removed

    def get_message_count(self, thread_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT message_count FROM thread_threads WHERE id = ? AND deleted = 0",
            (thread_id,),
        )
        return row["message_count"] if row else 0
