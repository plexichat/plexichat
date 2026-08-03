from typing import Optional, List, Dict, Any

import utils.logger as logger
from ..models import ThreadState
from ..exceptions import (
    ThreadError,
    ThreadNotFoundError,
    ThreadAccessDeniedError,
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
            self._unarchive_thread_internal(thread_id)

        if not self._is_member(user_id, thread_id):
            self.join_thread(user_id, thread_id)

        if not self._messaging or not thread.conversation_id:
            raise ThreadError(
                "Cannot send message: thread has no messaging conversation"
            )

        self._enforce_slowmode(user_id, thread_id, can_manage)

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
        """
        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        if not thread:
            raise ThreadNotFoundError(f"Thread {thread_id} not found")

        if not self.can_view_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot access this thread")

        row = self._db.fetch_one(
            "SELECT id FROM thread_messages WHERE thread_id = ? AND message_id = ?",
            (thread_id, message_id),
        )
        if not row:
            return False

        self._db.execute(
            "DELETE FROM thread_messages WHERE thread_id = ? AND message_id = ?",
            (thread_id, message_id),
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

        thread = _get_thread_internal(self._db, thread_id, self._encrypt_thread_names)
        conversation_id = thread.conversation_id if thread else None

        query = """
            SELECT tm.*, m.content, m.content_encrypted, m.author_id, m.created_at as message_created_at
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

    def get_message_count(self, thread_id: int) -> int:
        row = self._db.fetch_one(
            "SELECT message_count FROM thread_threads WHERE id = ? AND deleted = 0",
            (thread_id,),
        )
        return row["message_count"] if row else 0
