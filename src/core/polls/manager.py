"""
Poll manager - Core business logic for poll operations.

Handles poll creation, voting, results, and expiry with proper
validation and permission checks.
"""

from typing import Optional, List, Dict, Any

import utils.config as config
import utils.logger as logger
from ..base import BaseManager, SnowflakeID

from .models import (
    Poll,
    PollOption,
    PollResults,
    PollResultsVisibility,
)
from .exceptions import (
    PollNotFoundError,
    PollOptionNotFoundError,
    PollEndedError,
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    MultipleVoteNotAllowedError,
    PermissionDeniedError,
    MessageNotFoundError,
)


class PollManager(BaseManager):
    """Core poll manager handling all operations."""

    def __init__(self, db, auth_module=None, messaging_module=None):
        """
        Initialize the poll manager.

        Args:
            db: Database instance (must be connected)
            auth_module: Optional auth module for user verification
            messaging_module: Optional messaging module for message access
        """
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._config = self._load_config()

        logger.info("Poll module initialized")

    def _load_config(self) -> Dict[str, Any]:
        """Load poll configuration."""
        defaults = {
            "min_options": 2,
            "max_options": 10,
            "min_duration_hours": 1,
            "max_duration_hours": 168,
            "max_question_length": 300,
            "max_option_length": 100,
        }

        poll_config = config.get("polls", {})
        return {**defaults, **poll_config}

    def _validate_question(self, question: str) -> str:
        """Validate and sanitize poll question."""
        if not question or not question.strip():
            raise InvalidPollQuestionError("Poll question cannot be empty")

        question = question.strip()
        max_len = self._config.get("max_question_length", 300)

        if len(question) > max_len:
            raise InvalidPollQuestionError(
                f"Poll question cannot exceed {max_len} characters"
            )

        return question

    def _validate_option(self, option: str) -> str:
        """Validate and sanitize poll option."""
        if not option or not option.strip():
            raise InvalidPollOptionError("Poll option cannot be empty")

        option = option.strip()
        max_len = self._config.get("max_option_length", 100)

        if len(option) > max_len:
            raise InvalidPollOptionError(
                f"Poll option cannot exceed {max_len} characters"
            )

        return option

    def _validate_duration(self, duration_hours: Optional[int]) -> Optional[int]:
        """Validate poll duration."""
        if duration_hours is None:
            return None

        min_hours = self._config.get("min_duration_hours", 1)
        max_hours = self._config.get("max_duration_hours", 168)

        if duration_hours < min_hours or duration_hours > max_hours:
            raise InvalidPollDurationError(
                f"Poll duration must be between {min_hours} and {max_hours} hours",
                min_hours,
                max_hours,
            )

        return duration_hours

    def create_poll(
        self,
        user_id: SnowflakeID,
        message_id: SnowflakeID,
        question: str,
        options: List[str],
        duration_hours: Optional[int] = None,
        allow_multiple_choice: bool = False,
        results_visibility: PollResultsVisibility = PollResultsVisibility.ALWAYS,
    ) -> Poll:
        """
        Create a new poll attached to a message.

        Args:
            user_id: ID of user creating poll
            message_id: ID of message to attach poll to
            question: Poll question
            options: List of option texts (2-10)
            duration_hours: Optional duration in hours (1-168), None for no expiry
            allow_multiple_choice: Allow users to vote for multiple options
            results_visibility: When results are visible

        Returns:
            Created Poll

        Raises:
            InvalidPollQuestionError: Invalid question
            InvalidPollOptionError: Invalid option
            PollOptionLimitError: Invalid number of options
            InvalidPollDurationError: Invalid duration
            MessageNotFoundError: Message not found
        """
        if self._messaging:
            msg = self._messaging.get_message(user_id, message_id)
            if not msg:
                raise MessageNotFoundError("Message not found")
            if msg.author_id != user_id:
                raise PermissionDeniedError("Can only create polls on own messages")

        question = self._validate_question(question)

        min_opts = self._config.get("min_options", 2)
        max_opts = self._config.get("max_options", 10)

        if len(options) < min_opts or len(options) > max_opts:
            raise PollOptionLimitError(
                f"Poll must have between {min_opts} and {max_opts} options",
                min_opts,
                max_opts,
                len(options),
            )

        validated_options = [self._validate_option(opt) for opt in options]
        duration_hours = self._validate_duration(duration_hours)

        now = self._get_timestamp()
        poll_id = self._generate_id()

        ends_at = None
        if duration_hours:
            ends_at = now + (duration_hours * 3600 * 1000)

        # Use transaction for atomic creation
        try:
            self._db.begin_transaction()

            self._db.execute(
                """INSERT INTO poll_polls 
                   (id, message_id, question, created_by, created_at, ends_at, 
                    allow_multiple_choice, results_visibility)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    poll_id,
                    message_id,
                    question,
                    user_id,
                    now,
                    ends_at,
                    1 if allow_multiple_choice else 0,
                    results_visibility.value,
                ),
                auto_commit=False,
            )

            for i, option_text in enumerate(validated_options):
                option_id = self._generate_id()
                self._db.execute(
                    """INSERT INTO poll_options (id, poll_id, text, position)
                       VALUES (?, ?, ?, ?)""",
                    (option_id, poll_id, option_text, i),
                    auto_commit=False,
                )

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        logger.debug(f"Created poll {poll_id} on message {message_id}")

        result = self.get_poll(poll_id, user_id)
        assert result is not None  # Should exist since we just created it
        return result

    def get_poll(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> Optional[Poll]:
        """Get a poll by ID."""
        row = self._db.fetch_one("SELECT * FROM poll_polls WHERE id = ?", (poll_id,))

        if not row:
            return None

        # Verify access to the message
        if self._messaging:
            msg = self._messaging.get_message(user_id, row["message_id"])
            if not msg:
                return None

        return self._row_to_poll(row)

    def vote(
        self, user_id: SnowflakeID, poll_id: SnowflakeID, option_ids: List[SnowflakeID]
    ) -> PollResults:
        """
        Vote on a poll.

        Args:
            user_id: ID of user voting
            poll_id: ID of poll
            option_ids: List of option IDs to vote for

        Returns:
            Updated PollResults

        Raises:
            PollNotFoundError: Poll not found
            PollEndedError: Poll has ended
            PollOptionNotFoundError: Option not found
            AlreadyVotedError: User already voted
            MultipleVoteNotAllowedError: Multiple votes not allowed
        """
        if not option_ids:
            raise InvalidPollOptionError("At least one option must be selected")

        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        if poll.is_ended or (poll.ends_at and self._get_timestamp() >= poll.ends_at):
            if not poll.is_ended:
                self._end_poll(poll_id)
            raise PollEndedError("Poll has ended")

        # Handle duplicates and limits
        unique_option_ids = list(set(option_ids))
        if not poll.allow_multiple_choice and len(unique_option_ids) > 1:
            raise MultipleVoteNotAllowedError(
                "This poll does not allow multiple choice voting"
            )

        # Validate all options exist in this poll (optimized single query)
        placeholders = ",".join("?" * len(unique_option_ids))
        valid_options = self._db.fetch_all(
            f"SELECT id FROM poll_options WHERE poll_id = ? AND id IN ({placeholders})",
            (poll_id, *unique_option_ids),
        )

        if len(valid_options) != len(unique_option_ids):
            raise PollOptionNotFoundError(
                "One or more selected options are invalid for this poll"
            )

        now = self._get_timestamp()

        # Use transaction for atomic voting
        try:
            self._db.begin_transaction()

            # Delete existing votes to allow changing vote (re-voting replaces previous)
            self._db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )

            for option_id in unique_option_ids:
                vote_id = self._generate_id()
                self._db.execute(
                    """INSERT INTO poll_votes (id, poll_id, option_id, user_id, voted_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (vote_id, poll_id, option_id, user_id, now),
                    auto_commit=False,
                )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        logger.debug(f"User {user_id} voted on poll {poll_id}")

        return self.get_results(poll_id, user_id)

    def get_results(self, poll_id: SnowflakeID, user_id: SnowflakeID) -> PollResults:
        """
        Get poll results.

        Args:
            poll_id: ID of poll
            user_id: ID of user requesting results

        Returns:
            PollResults

        Raises:
            PollNotFoundError: Poll not found
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # Get user's own votes
        user_votes = self._db.fetch_all(
            "SELECT option_id FROM poll_votes WHERE poll_id = ? AND user_id = ?",
            (poll_id, user_id),
        )
        user_voted = len(user_votes) > 0
        user_vote_ids = [v["option_id"] for v in user_votes]

        # Check visibility
        can_see_results = True
        if poll.results_visibility == PollResultsVisibility.AFTER_VOTE:
            can_see_results = user_voted
        elif poll.results_visibility == PollResultsVisibility.AFTER_END:
            can_see_results = poll.is_ended or (
                poll.ends_at and self._get_timestamp() >= poll.ends_at
            )

        # Fetch all vote counts in a single query (optimized)
        vote_counts = {}
        if can_see_results:
            rows = self._db.fetch_all(
                "SELECT option_id, COUNT(*) as count FROM poll_votes WHERE poll_id = ? GROUP BY option_id",
                (poll_id,),
            )
            vote_counts = {row["option_id"]: row["count"] for row in rows}

        options_with_counts = []
        for option in poll.options:
            # If results are hidden, we return None for vote_count instead of 0
            vote_count = vote_counts.get(option.id, 0) if can_see_results else None
            options_with_counts.append(
                PollOption(
                    id=option.id,
                    poll_id=option.poll_id,
                    text=option.text,
                    position=option.position,
                    vote_count=vote_count,
                )
            )

        # Total votes = number of unique participants
        voter_count_row = self._db.fetch_one(
            "SELECT COUNT(DISTINCT user_id) as count FROM poll_votes WHERE poll_id = ?",
            (poll_id,),
        )
        total_voters = voter_count_row["count"] if voter_count_row else 0

        return PollResults(
            poll=poll,
            options=options_with_counts,
            total_votes=total_voters,
            user_voted=user_voted,
            user_votes=user_vote_ids,
        )

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

        if poll.created_by != user_id:
            raise PermissionDeniedError("Only poll creator can close the poll")

        if poll.is_ended:
            raise PollEndedError("Poll has already ended")

        self._end_poll(poll_id)

        logger.debug(f"Poll {poll_id} closed by user {user_id}")

        result = self.get_poll(poll_id, user_id)
        assert result is not None  # Should exist since we just updated it
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

        if poll.created_by != user_id:
            raise PermissionDeniedError("Only poll creator can delete the poll")

        self._db.execute("DELETE FROM poll_votes WHERE poll_id = ?", (poll_id,))
        self._db.execute("DELETE FROM poll_options WHERE poll_id = ?", (poll_id,))
        self._db.execute("DELETE FROM poll_polls WHERE id = ?", (poll_id,))

        logger.debug(f"Poll {poll_id} deleted")
        return True

    def _end_poll(self, poll_id: SnowflakeID):
        """Mark a poll as ended."""
        now = self._get_timestamp()
        self._db.execute(
            "UPDATE poll_polls SET ended_at = ? WHERE id = ?", (now, poll_id)
        )

    def check_expired_polls(self) -> int:
        """
        Check for and end expired polls.

        Returns:
            Number of polls ended

        This should be called periodically by a background task.
        """
        now = self._get_timestamp()

        rows = self._db.fetch_all(
            "SELECT id FROM poll_polls WHERE ends_at IS NOT NULL AND ends_at <= ? AND ended_at IS NULL",
            (now,),
        )

        count = 0
        for row in rows:
            self._end_poll(row["id"])
            count += 1

        if count > 0:
            logger.debug(f"Ended {count} expired polls")

        return count

    def _row_to_poll(self, row) -> Poll:
        """Convert database row to Poll."""
        poll_id = row["id"]

        option_rows = self._db.fetch_all(
            "SELECT * FROM poll_options WHERE poll_id = ? ORDER BY position", (poll_id,)
        )

        options = [
            PollOption(
                id=opt["id"],
                poll_id=opt["poll_id"],
                text=opt["text"],
                position=opt["position"],
            )
            for opt in option_rows
        ]

        total_votes_row = self._db.fetch_one(
            "SELECT COUNT(*) as count FROM poll_votes WHERE poll_id = ?", (poll_id,)
        )
        total_votes = total_votes_row["count"] if total_votes_row else 0

        is_ended = row["ended_at"] is not None
        if not is_ended and row["ends_at"]:
            is_ended = self._get_timestamp() >= row["ends_at"]

        return Poll(
            id=row["id"],
            message_id=row["message_id"],
            question=row["question"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            ends_at=row["ends_at"],
            ended_at=row["ended_at"],
            allow_multiple_choice=bool(row["allow_multiple_choice"]),
            results_visibility=PollResultsVisibility(row["results_visibility"]),
            options=options,
            total_votes=total_votes,
            is_ended=is_ended,
        )
