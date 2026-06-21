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
    PollVote,
)
from .exceptions import (
    AlreadyVotedError,
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


def _is_unique_violation(exc: BaseException) -> bool:
    """Return True if ``exc`` is a UNIQUE-violation error from the driver.

    Both ``sqlite3.IntegrityError`` (covers SQLite's
    ``UNIQUE constraint failed`` text) and
    ``psycopg2.errors.UniqueViolation`` (postgres) qualify. Falls
    back to textual sniffing (``"unique" / "duplicate"``) so drivers
    that wrap the underlying error still match.
    """
    try:
        import sqlite3

        if isinstance(exc, sqlite3.IntegrityError):
            txt = str(exc).lower()
            return "unique" in txt or "duplicate" in txt
    except Exception:
        pass
    try:
        import psycopg2

        if isinstance(exc, psycopg2.errors.UniqueViolation):
            return True
    except Exception:
        pass
    txt = str(exc).lower()
    return "unique" in txt or "duplicate" in txt


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
        self._encrypt_polls = config.get("encryption.encrypt_polls", False)

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

            # Encrypt question if enabled
            question_encrypted = None
            if self._encrypt_polls:
                from src.utils.encryption import encrypt_data

                question_encrypted = encrypt_data(question)

            self._db.execute(
                """INSERT INTO poll_polls 
                   (id, message_id, question, question_encrypted, created_by, created_at, ends_at, 
                    allow_multiple_choice, results_visibility)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    poll_id,
                    message_id,
                    question,
                    question_encrypted,
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
                # Encrypt option if enabled
                option_encrypted = None
                if self._encrypt_polls:
                    from src.utils.encryption import encrypt_data

                    option_encrypted = encrypt_data(option_text)
                self._db.execute(
                    """INSERT INTO poll_options (id, poll_id, text, text_encrypted, position)
                       VALUES (?, ?, ?, ?, ?)""",
                    (option_id, poll_id, option_text, option_encrypted, i),
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

        # CORRECTNESS FIX: the previous implementation always
        # ``DELETE``d any prior vote before ``INSERT``ing, so a user
        # could re-vote on the same poll indefinitely even though
        # :class:`AlreadyVotedError` was documented in
        # ``polls/exceptions.py``. We now enforce one vote per user
        # per poll **explicitly**, falling back to the database's
        # ``UNIQUE(poll_id, user_id)`` constraint where present.
        # ``change_vote`` is a separate dedicated endpoint for users
        # who want to revise their selection.
        existing = self._db.fetch_one(
            "SELECT 1 FROM poll_votes WHERE poll_id = ? AND user_id = ? LIMIT 1",
            (poll_id, user_id),
        )
        if existing:
            raise AlreadyVotedError(
                "User has already voted on this poll; "
                "call change_vote() to revise the selection"
            )

        now = self._get_timestamp()

        try:
            self._db.begin_transaction()

            for option_id in unique_option_ids:
                vote_id = self._generate_id()
                self._db.execute(
                    """INSERT INTO poll_votes (id, poll_id, option_id, user_id, voted_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (vote_id, poll_id, option_id, user_id, now),
                    auto_commit=False,
                )
            self._db.commit()
        except Exception as exc:
            self._db.rollback()
            # CORRECTNESS FIX: pre-INSERT existence check above is
            # the primary guard; the DB-level UNIQUE(poll_id,
            # user_id) constraint remains authoritative for the
            # expensive inter-thread race window.
            # ``_is_unique_violation`` matches driver-typed errors
            # (sqlite3.IntegrityError / psycopg2.errors.UniqueViolation)
            # before falling back to textual sniffing of "unique" /
            # "duplicate" so wrapped drivers still match.
            if _is_unique_violation(exc):
                raise AlreadyVotedError("User has already voted on this poll") from exc
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

    def has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """Return True if `user_id` has cast at least one vote on `poll_id`.

        This is a cheap existence check — it returns False for polls that
        don't exist, which keeps callers from having to first verify the
        poll themselves.
        """
        if not poll_id or not user_id:
            return False
        row = self._db.fetch_one(
            "SELECT 1 FROM poll_votes WHERE poll_id = ? AND user_id = ? LIMIT 1",
            (poll_id, user_id),
        )
        return bool(row)

    def remove_vote(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """Remove every vote `user_id` previously cast on `poll_id`.

        Returns True when at least one row was deleted.  Raises
        :class:`PollNotFoundError` if the poll itself does not exist so
        callers can distinguish "user hadn't voted" from "no such poll".
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        cursor = self._db.execute(
            "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
            (poll_id, user_id),
        )
        try:
            deleted = cursor.rowcount
        except Exception:
            # Database drivers without rowcount support fall through to False
            deleted = 0
        try:
            cursor.close()
        except Exception:
            pass

        logger.debug(
            "User %s vote(s) on poll %s cleared (%d row(s))",
            user_id,
            poll_id,
            deleted,
        )
        return deleted > 0

    def get_voters(
        self, poll_id: SnowflakeID, user_id: SnowflakeID
    ) -> List[SnowflakeID]:
        """Return the discrete list of user IDs that voted on `poll_id`.

        Visibility-aware (mirrors :meth:`get_results`):

        * ``AFTER_VOTE`` — never (the roster is private from the start);
        * ``AFTER_END``  — only once the poll is ended or its end-time has
          passed;
        * ``ALWAYS``     — every distinct voter.

        Raises :class:`PollNotFoundError` if the poll itself does not exist.
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        # Per-request permission boundary: the discrete voter roster
        # is privacy-sensitive (it answers "who voted for what" rather
        # than just aggregate counts) so we gate access on a
        # creator-or-moderator check.  The creator always sees the
        # roster (needed for refunds / dedupe / close-out), as does
        # any caller with the ``admin.polls.view_voters`` permission
        # granted through the auth module.
        try:
            is_creator = int(poll.created_by) == int(user_id)
        except (TypeError, ValueError):
            is_creator = False
        if not is_creator and not self._has_admin_poll_voters_perm(user_id):
            return []

        if poll.results_visibility == PollResultsVisibility.AFTER_VOTE:
            return []

        if poll.results_visibility == PollResultsVisibility.AFTER_END:
            if not poll.is_ended and not (
                poll.ends_at and self._get_timestamp() >= poll.ends_at
            ):
                return []

        rows = self._db.fetch_all(
            "SELECT DISTINCT user_id FROM poll_votes WHERE poll_id = ? ORDER BY user_id",
            (poll_id,),
        )
        return [int(row["user_id"]) for row in rows]

    def get_user_votes(
        self, user_id: SnowflakeID, poll_id: Optional[SnowflakeID] = None
    ) -> List[PollVote]:
        """Return every vote `user_id` has ever cast, optionally scoped to a poll.

        Without a `poll_id` filter the query returns the full history of
        the user's voting activity (used by the integration test
        ``test_get_user_votes``); with one, it returns just the rows
        attached to that poll.
        """
        if poll_id is not None:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_votes WHERE user_id = ? AND poll_id = ? ORDER BY voted_at DESC",
                (user_id, poll_id),
            )
        else:
            rows = self._db.fetch_all(
                "SELECT * FROM poll_votes WHERE user_id = ? ORDER BY voted_at DESC",
                (user_id,),
            )
        return [
            PollVote(
                id=row["id"],
                poll_id=row["poll_id"],
                option_id=row["option_id"],
                user_id=row["user_id"],
                voted_at=row["voted_at"],
            )
            for row in rows
        ]

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

    def _has_admin_poll_voters_perm(self, user_id: SnowflakeID) -> bool:
        """Check the configured auth module for ``admin.polls.view_voters``.

        Returns False when no auth module is wired up so anonymous
        callers can never see voter rosters by accident.
        """
        auth_mod = getattr(self, "_auth", None)
        if auth_mod is None or not user_id:
            return False
        try:
            perm_check = getattr(auth_mod, "has_permission", None)
            if perm_check is None:
                return False
            return bool(perm_check(int(user_id), "admin.polls.view_voters"))
        except Exception:  # pragma: no cover -- defensive
            return False

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
            question=question,
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
