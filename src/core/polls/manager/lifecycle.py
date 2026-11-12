"""Closing, deleting, and expiring polls."""

import utils.logger as logger
from ...base import SnowflakeID

from ..exceptions import (
    PermissionDeniedError,
    PollEndedError,
    PollError,
    PollNotFoundError,
)
from ..models import Poll
from .base import PollManagerBase


class LifecycleMixin(PollManagerBase):
    """Close, delete, and expire polls and broadcast the updates."""

    def close_poll(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> Poll:
        """
        Close a poll early (creator only).

        Args:
            user_id: ID of user closing poll
            poll_id: ID of poll

        Returns:
            Updated Poll

        Raises:
            PollNotFoundError: Poll not found
            PermissionDeniedError: Not poll creator
            PollEndedError: Poll already ended
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # Normalize both sides to int (F10): SQLite returns ints while the
        # postgres driver returns strings, and a direct != comparison would
        # deny legitimate creators on postgres (fail-closed). Mirrors the
        # defensive int() cast used by get_voters.
        try:
            is_creator = int(poll.created_by) == int(user_id)
        except (TypeError, ValueError):
            is_creator = False
        if not is_creator:
            raise PermissionDeniedError("Only poll creator can close the poll")

        if poll.is_ended:
            raise PollEndedError("Poll has already ended")

        self._end_poll(poll_id)

        logger.debug(f"Poll {poll_id} closed by user {user_id}")

        self._emit_poll_update(poll, user_id, "close")

        result = self.get_poll(poll_id, user_id)
        if result is None:
            raise PollError("Failed to fetch poll after closing")
        return result

    def delete_poll(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """
        Delete a poll (creator only).

        Args:
            user_id: ID of user deleting poll
            poll_id: ID of poll

        Returns:
            True if deleted

        Raises:
            PollNotFoundError: Poll not found
            PermissionDeniedError: Not poll creator
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # F10: normalize both sides to int (see close_poll) so the creator
        # check holds on both SQLite and postgres drivers.
        try:
            is_creator = int(poll.created_by) == int(user_id)
        except (TypeError, ValueError):
            is_creator = False
        if not is_creator:
            raise PermissionDeniedError("Only poll creator can delete the poll")

        self._db.execute("DELETE FROM poll_votes WHERE poll_id = ?", (poll_id,))
        self._db.execute("DELETE FROM poll_voters WHERE poll_id = ?", (poll_id,))
        self._db.execute("DELETE FROM poll_options WHERE poll_id = ?", (poll_id,))
        self._db.execute("DELETE FROM poll_polls WHERE id = ?", (poll_id,))

        logger.debug(f"Poll {poll_id} deleted")
        return True

    def check_expired_polls(self) -> int:
        """
        Check for and end expired polls.

        Returns:
            Number of polls ended

        This should be called periodically by a background task.
        """
        now = self._get_timestamp()

        rows = self._db.fetch_all(
            """SELECT id, message_id, created_by FROM poll_polls
               WHERE ends_at IS NOT NULL AND ends_at <= ? AND ended_at IS NULL""",
            (now,),
        )

        count = 0
        for row in rows:
            self._end_poll(row["id"])
            try:
                channel_id = None
                if self._messaging:
                    msg = self._messaging.get_message(
                        int(row["created_by"]), int(row["message_id"])
                    )
                    if msg is not None:
                        channel_id = getattr(msg, "conversation_id", None)
                from src.core.events.gateway_emit import emit_poll_update

                emit_poll_update(
                    poll_id=int(row["id"]),
                    message_id=int(row["message_id"]),
                    action="close",
                    actor_id=int(row["created_by"]),
                    is_ended=True,
                    channel_id=int(channel_id) if channel_id else None,
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"POLL_UPDATE emission failed for expired poll: {e}")
            count += 1

        if count > 0:
            logger.debug(f"Ended {count} expired polls")

        return count

    def _end_poll(self, poll_id: SnowflakeID):
        """Mark a poll as ended."""
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE poll_polls SET ended_at = ? WHERE id = ?", (now, poll_id)
        )

    def _emit_poll_update(
        self,
        poll: Poll,
        actor_id: SnowflakeID,
        action: str,
    ) -> None:
        """Broadcast a POLL_UPDATE gateway event for a poll mutation.

        The payload intentionally carries NO vote counts: result
        visibility is per-viewer (AFTER_VOTE / AFTER_END), so counts are
        resolved by each client refetching ``GET /polls/{id}/results``
        with its own credentials. Emission is best-effort and never
        raises.
        """
        try:
            channel_id = None
            if self._messaging:
                msg = self._messaging.get_message(int(actor_id), poll.message_id)
                if msg is not None:
                    channel_id = getattr(msg, "conversation_id", None)
            from src.core.events.gateway_emit import emit_poll_update

            emit_poll_update(
                poll_id=int(poll.id),
                message_id=int(poll.message_id),
                action=action,
                actor_id=int(actor_id),
                is_ended=bool(poll.is_ended),
                channel_id=int(channel_id) if channel_id else None,
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Failed to emit POLL_UPDATE for poll {poll.id}: {e}")
