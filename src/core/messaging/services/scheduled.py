"""
Scheduled message service - Business logic for scheduled messages.

Handles creating, listing, canceling, and dispatching scheduled messages
with proper validation and permission checks.
"""

import time
import json
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import SnowflakeID
from src.utils.encryption import generate_snowflake_id


class ScheduledMessageService:
    """Service for managing scheduled messages."""

    MAX_SCHEDULED_PER_USER = 50
    MIN_SCHEDULE_AHEAD_MS = 60000  # 1 minute minimum
    MAX_SCHEDULE_AHEAD_MS = 7 * 24 * 3600 * 1000  # 7 days maximum

    def __init__(self, db, participant_svc=None):
        self._db = db
        self._participant_svc = participant_svc

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def create_scheduled_message(
        self,
        user_id: SnowflakeID,
        conversation_id: SnowflakeID,
        content: str,
        scheduled_at: int,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Schedule a message for future delivery.

        Args:
            user_id: ID of the user scheduling the message
            conversation_id: ID of the target conversation
            content: Message content
            scheduled_at: Timestamp (ms) when to send
            attachments: Optional attachments metadata

        Returns:
            Scheduled message record dict
        """
        now = self._get_timestamp()

        # Validate scheduled time
        if scheduled_at < now + self.MIN_SCHEDULE_AHEAD_MS:
            raise ValueError("Scheduled time must be at least 1 minute in the future")
        if scheduled_at > now + self.MAX_SCHEDULE_AHEAD_MS:
            raise ValueError("Scheduled time cannot be more than 7 days in the future")

        # Check user limit
        count_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM msg_scheduled WHERE author_id = ? AND status = 'pending'",
            (user_id,),
        )
        count = count_row["count"] if count_row else 0
        if count >= self.MAX_SCHEDULED_PER_USER:
            raise ValueError(
                f"Maximum {self.MAX_SCHEDULED_PER_USER} scheduled messages per user"
            )

        # Check participant access
        if self._participant_svc and not self._participant_svc.is_participant(
            conversation_id, user_id
        ):
            raise PermissionError("Not a participant in this conversation")

        msg_id = self._generate_id()
        self._db.execute(
            """INSERT INTO msg_scheduled
               (id, conversation_id, author_id, content, scheduled_at, status, created_at, updated_at, attachments)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (
                msg_id,
                conversation_id,
                user_id,
                content,
                scheduled_at,
                now,
                now,
                json.dumps(attachments) if attachments else None,
            ),
        )

        logger.debug(
            f"Scheduled message {msg_id} created by user {user_id} for {scheduled_at}"
        )
        result = self.get_scheduled_message(msg_id, user_id)
        return result if result else {}

    def get_scheduled_message(
        self, scheduled_id: SnowflakeID, user_id: SnowflakeID
    ) -> Optional[Dict[str, Any]]:
        """Get a scheduled message by ID."""
        row = self._db.fetch_one(
            "SELECT * FROM msg_scheduled WHERE id = ?", (scheduled_id,)
        )
        if not row:
            return None
        data = dict(row)
        # Verify ownership
        if data["author_id"] != user_id:
            return None
        if data.get("attachments") and isinstance(data["attachments"], str):
            try:
                data["attachments"] = json.loads(data["attachments"])
            except (json.JSONDecodeError, TypeError):
                data["attachments"] = None
        return data

    def list_scheduled_messages(
        self,
        user_id: SnowflakeID,
        conversation_id: Optional[SnowflakeID] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """List scheduled messages for a user."""
        query = "SELECT * FROM msg_scheduled WHERE author_id = ?"
        params: list = [user_id]

        if conversation_id:
            query += " AND conversation_id = ?"
            params.append(conversation_id)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY scheduled_at ASC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, tuple(params))
        results = []
        for row in rows:
            data = dict(row)
            if data.get("attachments") and isinstance(data["attachments"], str):
                try:
                    data["attachments"] = json.loads(data["attachments"])
                except (json.JSONDecodeError, TypeError):
                    data["attachments"] = None
            results.append(data)
        return results

    def cancel_scheduled_message(
        self, scheduled_id: SnowflakeID, user_id: SnowflakeID
    ) -> bool:
        """Cancel a pending scheduled message."""
        row = self._db.fetch_one(
            "SELECT author_id, status FROM msg_scheduled WHERE id = ?",
            (scheduled_id,),
        )
        if not row:
            raise ValueError("Scheduled message not found")
        if row["author_id"] != user_id:
            raise PermissionError("Not the author of this scheduled message")
        if row["status"] != "pending":
            raise ValueError("Can only cancel pending scheduled messages")

        now = self._get_timestamp()
        self._db.execute(
            "UPDATE msg_scheduled SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, scheduled_id),
        )
        logger.debug(f"Scheduled message {scheduled_id} cancelled by user {user_id}")
        return True

    def get_due_messages(self) -> List[Dict[str, Any]]:
        """Get all scheduled messages that are due for delivery."""
        now = self._get_timestamp()
        rows = self._db.fetch_all(
            "SELECT * FROM msg_scheduled WHERE status = 'pending' AND scheduled_at <= ?",
            (now,),
        )
        return [dict(row) for row in rows]

    def mark_dispatched(
        self, scheduled_id: SnowflakeID, message_id: SnowflakeID
    ) -> None:
        """Mark a scheduled message as dispatched with the created message ID."""
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE msg_scheduled SET status = 'dispatched', message_id = ?, updated_at = ? WHERE id = ?",
            (message_id, now, scheduled_id),
        )

    def dispatch_due_messages(self, messaging_module=None) -> List[Dict[str, Any]]:
        """
        Dispatch all scheduled messages that are due for delivery.

        Should be called periodically by a scheduler/cron task.
        Processes due messages in order of scheduled_at, creates actual
        messages via the messaging module, and updates the scheduled
        message status.

        Args:
            messaging_module: Optional messaging module for sending messages.
                If not provided, attempts to use api.get_messaging().

        Returns:
            List of dispatch result dicts with 'scheduled_id', 'message_id', 'status'
        """
        due_messages = self.get_due_messages()
        if not due_messages:
            return []

        # Resolve messaging module if not provided
        if messaging_module is None:
            try:
                import src.api as api

                messaging_module = api.get_messaging()
            except Exception:
                logger.error(
                    "Cannot dispatch scheduled messages: messaging module unavailable"
                )
                return []

        results = []
        for scheduled in due_messages:
            scheduled_id = scheduled["id"]
            try:
                # Build attachment data if present
                attachments = None
                if scheduled.get("attachments"):
                    import json as _json

                    if isinstance(scheduled["attachments"], str):
                        try:
                            attachments = _json.loads(scheduled["attachments"])
                        except (_json.JSONDecodeError, TypeError):
                            attachments = None
                    else:
                        attachments = scheduled["attachments"]

                # Send the actual message
                if messaging_module is None:
                    logger.error("Messaging module is None, cannot dispatch message")
                    continue
                msg = messaging_module.send_message(
                    user_id=scheduled["author_id"],
                    conversation_id=scheduled["conversation_id"],
                    content=scheduled["content"],
                    attachments=attachments,
                )

                # Mark as dispatched
                message_id: Optional[int] = (
                    msg.id if hasattr(msg, "id") else getattr(msg, "id", None)
                )
                if message_id is not None:
                    self.mark_dispatched(scheduled_id, message_id)

                results.append(
                    {
                        "scheduled_id": scheduled_id,
                        "message_id": message_id,
                        "status": "dispatched",
                    }
                )
                logger.info(
                    f"Dispatched scheduled message {scheduled_id} as message {message_id}"
                )

            except Exception as e:
                self.mark_failed(scheduled_id, str(e))
                results.append(
                    {
                        "scheduled_id": scheduled_id,
                        "message_id": None,
                        "status": "failed",
                        "error": str(e),
                    }
                )
                logger.error(
                    f"Failed to dispatch scheduled message {scheduled_id}: {e}"
                )

        return results

    def mark_failed(self, scheduled_id: SnowflakeID, error: str) -> None:
        """Mark a scheduled message as failed."""
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE msg_scheduled SET status = 'failed', updated_at = ? WHERE id = ?",
            (now, scheduled_id),
        )
        logger.error(f"Scheduled message {scheduled_id} failed: {error}")
