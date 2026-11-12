"""Poll reads, row hydration, and visibility checks."""

from typing import Optional

import utils.logger as logger
from ...base import SnowflakeID

from ..models import Poll, PollOption, PollResultsVisibility
from .base import PollManagerBase


class ReadingMixin(PollManagerBase):
    """Read polls and decide what a viewer is allowed to see."""

    def get_poll(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Poll]:
        """Get a poll by ID."""
        row = self._db.fetch_one("SELECT * FROM poll_polls WHERE id = ?", (poll_id,))

        if not row:
            return None

        # Verify access to the message (fail closed: without the messaging
        # module there is no way to enforce channel membership).
        messaging = self._require_messaging()
        msg = messaging.get_message(user_id, row["message_id"])
        if not msg:
            return None

        poll = self._row_to_poll(row)

        # Whatever the visibility setting, ``_row_to_poll`` never fills in
        # aggregate vote totals; that is reconciled here against the
        # requesting user.  A user who is not entitled to see the results
        # (e.g. has not voted on an ``AFTER_VOTE`` poll) must not receive
        # the voter headcount, so it is suppressed.
        user_voted = self._has_voted(user_id, poll_id)
        if self._can_view_results(poll, user_voted):
            poll.total_votes = self._voter_count(poll_id)
        else:
            poll.total_votes = None

        return poll

    def _row_to_poll(self, row) -> Poll:
        """Convert database row to Poll."""
        poll_id = row["id"]

        # Decrypt question if encryption is enabled and encrypted data exists
        question = row["question"]
        if self._encrypt_polls and row.get("question_encrypted"):
            from src.utils.encryption import decrypt_data

            try:
                question = decrypt_data(row["question_encrypted"])
            except Exception as e:
                logger.warning(f"Failed to decrypt poll question {poll_id}: {e}")
                question = row["question"]  # Fallback to unencrypted

        option_rows = self._db.fetch_all(
            "SELECT * FROM poll_options WHERE poll_id = ? ORDER BY position", (poll_id,)
        )

        options = []
        for opt in option_rows:
            # Decrypt option text if encryption is enabled and encrypted data exists
            option_text = opt["text"]
            if self._encrypt_polls and opt.get("text_encrypted"):
                from src.utils.encryption import decrypt_data

                try:
                    option_text = decrypt_data(opt["text_encrypted"])
                except Exception as e:
                    logger.warning(f"Failed to decrypt poll option {opt['id']}: {e}")
                    option_text = opt["text"]  # Fallback to unencrypted

            options.append(
                PollOption(
                    id=opt["id"],
                    poll_id=opt["poll_id"],
                    text=option_text,
                    position=opt["position"],
                )
            )

        # Aggregate vote totals are intentionally NOT computed here.  They
        # are reconciled in ``get_poll`` / ``get_results`` against the
        # requesting user's visibility rights so that hidden results can
        # never leak a voter headcount through a plain ``get_poll`` call.
        is_ended = row["ended_at"] is not None
        if not is_ended and row["ends_at"]:
            is_ended = self._get_timestamp() >= row["ends_at"]

        return Poll(
            id=row["id"],
            message_id=row["message_id"],
            question=question,
            created_by=row["created_by"],
            created_at=row["created_at"],
            ends_at=row["ends_at"],
            ended_at=row["ended_at"],
            allow_multiple_choice=bool(row["allow_multiple_choice"]),
            results_visibility=PollResultsVisibility(row["results_visibility"]),
            options=options,
            total_votes=None,
            is_ended=is_ended,
        )

    def _has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """True if ``user_id`` is registered as a voter on ``poll_id``."""
        if not poll_id or not user_id:
            return False
        row = self._db.fetch_one(
            "SELECT 1 FROM poll_voters WHERE poll_id = ? AND user_id = ? LIMIT 1",
            (poll_id, user_id),
        )
        return bool(row)

    def _voter_count(self, poll_id: SnowflakeID) -> int:
        """Number of distinct voters on a poll."""
        row = self._db.fetch_one(
            "SELECT COUNT(*) AS count FROM poll_voters WHERE poll_id = ?",
            (poll_id,),
        )
        return int(row["count"]) if row else 0

    def _can_view_results(self, poll: Poll, user_voted: bool) -> bool:
        """Whether ``user_voted``'s owner may see this poll's results now."""
        visibility = poll.results_visibility
        if visibility == PollResultsVisibility.AFTER_VOTE:
            return bool(user_voted)
        if visibility == PollResultsVisibility.AFTER_END:
            return poll.is_ended or bool(
                poll.ends_at and self._get_timestamp() >= poll.ends_at
            )
        return True  # ALWAYS or unknown -> visible
