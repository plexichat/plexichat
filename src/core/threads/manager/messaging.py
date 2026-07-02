from typing import Optional, List, Dict, Any

import utils.logger as logger
from ..models import ThreadState
from ..exceptions import (
    ThreadNotFoundError,
    ThreadAccessDeniedError,
    ThreadLockedError,
)

from .helpers import _get_thread_internal
from .protocol import ThreadProtocol


class ThreadMessagingMixin(ThreadProtocol):
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

        if thread.locked:
            if not self.can_manage_thread(user_id, thread_id):
                raise ThreadLockedError("Thread is locked")

        if not self.can_send_in_thread(user_id, thread_id):
            raise ThreadAccessDeniedError("Cannot send messages in this thread")

        if thread.state == ThreadState.ARCHIVED:
            self._unarchive_thread_internal(thread_id)

        if not self._is_member(user_id, thread_id):
            self.join_thread(user_id, thread_id)

        now = self._get_timestamp()
        message_id = self._generate_id()

        if self._messaging and thread.conversation_id:
            try:
                msg = self._messaging.send_message(
                    user_id=user_id,
                    conversation_id=thread.conversation_id,
                    content=content,
                    attachments=attachments,
                )
                message_id = msg.id
            except Exception as e:
                logger.error(
                    f"Failed to send message via messaging module for thread {thread_id}: {e}"
                )

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

        logger.debug(f"Message sent to thread {thread_id} by user {user_id}")

        return {
            "id": message_id,
            "thread_id": thread_id,
            "user_id": user_id,
            "content": content,
            "created_at": now,
        }

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

        query = """
            SELECT tm.*, m.content, m.content_encrypted, m.author_id, m.created_at as message_created_at
            FROM thread_messages tm
            JOIN msg_messages m ON tm.message_id = m.id
            WHERE tm.thread_id = ?
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
                data["content"] = decrypt_message(content, data["message_id"])
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
