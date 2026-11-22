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

# ``msg_scheduled`` columns in schema order (see ``src/core/messaging/schema.py``).
# ``fetch_one``/``fetch_all`` may return a mapping or a bare tuple depending on
# the database adapter (see docs/admin-hardening.md); tuple rows from
# ``SELECT *`` follow this order.
_SCHEDULED_COLUMNS = (
    "id",
    "conversation_id",
    "author_id",
    "content",
    "scheduled_at",
    "status",
    "message_id",
    "created_at",
    "updated_at",
    "attachments",
)


def _scheduled_row_to_dict(row) -> Dict[str, Any]:
    """Normalise a mapping or tuple ``SELECT *`` row to a dict."""
    if isinstance(row, dict):
        return dict(row)
    return dict(zip(_SCHEDULED_COLUMNS, row))


class ScheduledMessageService:
    """Service for managing scheduled messages."""

    MAX_SCHEDULED_PER_USER = 50
    # A hard upper bound on a single listing. Only 50 pending messages are
    # allowed per user, but cancelled/dispatched rows accumulate forever; an
    # unbounded ``limit`` on those could dump the user's entire history (and
    # each row carries the scheduled plaintext) in one read.
    MAX_LIST_LIMIT = 200
    MIN_SCHEDULE_AHEAD_MS = 60000  # 1 minute minimum
    MAX_SCHEDULE_AHEAD_MS = 7 * 24 * 3600 * 1000  # 7 days maximum

    def __init__(self, db, participant_svc=None):
        self._db = db
        self._participant_svc = participant_svc

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _assert_channel_post_access(
        self, conversation_id: SnowflakeID, user_id: SnowflakeID
    ) -> None:
        """Apply the live send path's channel gate before storing content.

        ``is_participant`` proves DM participation or membership in the
        conversation's server, but not that the caller may view or post in the
        specific channel backing this conversation. The normal send route
        resolves the channel with ``get_channel`` (membership + channels.view),
        rejects timed-out members, and checks the server permission set. A
        scheduled message is dispatched later through the messaging layer,
        whose only gate is that same membership-only check, so without this
        guard a member could queue content into a channel they cannot post to.
        Deny whenever the channel/permission data cannot be resolved rather
        than treating an unavailable checker as permission.

        The reverse lookup must recognise BOTH id forms. Besides the linked
        ``conversation_id``, these APIs also accept a server channel's own
        ``id`` in the conversation slot (``ParticipantService.is_participant``
        falls back to ``srv_channels WHERE conversation_id = ? OR id = ?``,
        and the poll/bookmark/attachment gates probe for that alias). A lookup
        keyed only on ``conversation_id`` misses that alias, skips this gate
        entirely, and lets a member denied view/send queue content that
        dispatch later posts through the membership-only send path. Resolve
        the authoritative channel row by either id so the same live gate is
        applied to both create and dispatch.
        """
        try:
            channel_row = self._db.fetch_one(
                "SELECT id, server_id FROM srv_channels "
                "WHERE (conversation_id = ? OR id = ?) AND deleted = 0 LIMIT 1",
                (conversation_id, conversation_id),
            )
        except Exception:
            raise PermissionError("Cannot verify channel access")
        if not channel_row:
            # DM / group conversation: no separate channel grant applies and
            # participation has already been verified by the caller.
            return
        channel_id = (
            channel_row["id"] if isinstance(channel_row, dict) else channel_row[0]
        )
        server_id = (
            channel_row["server_id"]
            if isinstance(channel_row, dict)
            else channel_row[1]
        )
        if not channel_id or not server_id:
            raise PermissionError("Cannot verify channel access")

        try:
            import src.api as api

            servers = api.get_servers()
        except Exception:
            raise PermissionError("Cannot verify channel access")
        if servers is None:
            raise PermissionError("Cannot verify channel access")
        try:
            # get_channel enforces membership and channels.view for this
            # exact caller; a restricted channel resolves to None.
            if servers.get_channel(channel_id, user_id) is None:
                raise PermissionError("Cannot post to this channel")
            if servers.is_timed_out(user_id, server_id):
                raise PermissionError("Timed out in this server")
            permissions = servers.get_permissions(user_id, server_id, channel_id)
            if not permissions or not permissions.get("messages.send", False):
                raise PermissionError("Missing messages.send permission")
        except PermissionError:
            raise
        except Exception:
            raise PermissionError("Cannot verify channel access")

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
        # ``SELECT COUNT(*) ...`` yields a single-column row; read it by
        # position when the adapter returns tuples instead of mappings
        # (see docs/admin-hardening.md).
        if count_row is None:
            count = 0
        elif isinstance(count_row, dict):
            count = count_row["count"]
        else:
            count = count_row[0]
        if count >= self.MAX_SCHEDULED_PER_USER:
            raise ValueError(
                f"Maximum {self.MAX_SCHEDULED_PER_USER} scheduled messages per user"
            )

        # Authorization must fail closed. This check used to be guarded by
        # ``if self._participant_svc``: when the messaging module was not
        # initialised (or a manager implementation lacked the participant
        # service), it was skipped entirely and any authenticated user could
        # schedule a message into a conversation they do not belong to. Every
        # other messaging write path denies when the checker is unavailable.
        if not self._participant_svc:
            raise PermissionError("Cannot verify conversation participation")
        if not self._participant_svc.is_participant(conversation_id, user_id):
            raise PermissionError("Not a participant in this conversation")

        # Membership alone is not posting permission: a server member may not
        # be allowed to view/post in this particular channel. Enforce the same
        # channel gate the live send path uses before persisting content that
        # dispatch will deliver through a membership-only send path.
        self._assert_channel_post_access(conversation_id, user_id)

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
        data = _scheduled_row_to_dict(row)
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
        # Clamp on BOTH sides. SQLite treats LIMIT -1 as *unlimited*, so a
        # negative limit dumped the list; an arbitrarily large positive limit
        # (e.g. 10**12) did the same by bypassing the page cap from above.
        try:
            requested = int(limit)
        except (TypeError, ValueError):
            requested = 0
        limit = max(0, min(requested, self.MAX_LIST_LIMIT))
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
            data = _scheduled_row_to_dict(row)
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
        # ``SELECT author_id, status`` -- read by position for tuple-row
        # adapters (see docs/admin-hardening.md).
        author_id = row["author_id"] if isinstance(row, dict) else row[0]
        status = row["status"] if isinstance(row, dict) else row[1]
        if author_id != user_id:
            raise PermissionError("Not the author of this scheduled message")
        if status != "pending":
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
        return [_scheduled_row_to_dict(row) for row in rows]

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
                # SECURITY: the channel gate applied when this message was
                # SCHEDULED is captured authority, not live authority. The
                # messaging layer's only check is ``is_participant`` (server
                # membership), so a member who lost ``messages.send``, lost
                # view of the channel, or was timed out since scheduling would
                # still get the queued post delivered. Re-apply the same live
                # channel gate here, at execution time, and FAIL CLOSED: a
                # denial (or an unavailable checker) marks the row failed via
                # the handler below and never reaches ``send_message``.
                self._assert_channel_post_access(
                    scheduled["conversation_id"], scheduled["author_id"]
                )

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
