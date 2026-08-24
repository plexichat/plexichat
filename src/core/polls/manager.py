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
    PollError,
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
    PollConfigurationError,
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

    def _require_messaging(self):
        """Fail closed when the messaging module is not wired.

        Ownership and channel-access checks are enforced through the
        messaging layer; running without it would silently skip both,
        so a missing dependency is a configuration error instead of a
        permissive fallback. Returns the messaging module so callers
        keep a narrowed, non-Optional reference.
        """
        if not self._messaging:
            raise PollConfigurationError(
                "Polls module requires the messaging module to be wired "
                "for message-existence and authorization checks; call "
                "polls.setup(db, messaging_module=...) first"
            )
        return self._messaging

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
        messaging = self._require_messaging()
        msg = messaging.get_message(user_id, message_id)
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

            # Encrypt question if enabled.  When encryption is on we must
            # NOT store the plaintext in the NOT NULL ``question`` column --
            # that would defeat encryption-at-rest entirely (the raw text
            # would remain readable in the DB).  We write an empty marker
            # instead and the ciphertext lives only in ``question_encrypted``.
            question_encrypted = None
            stored_question = question
            if self._encrypt_polls:
                from src.utils.encryption import encrypt_data

                question_encrypted = encrypt_data(question)
                stored_question = ""

            self._db.execute(
                """INSERT INTO poll_polls 
                   (id, message_id, question, question_encrypted, created_by, created_at, ends_at, 
                    allow_multiple_choice, results_visibility)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    poll_id,
                    message_id,
                    stored_question,
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
                # Encrypt option if enabled (same at-rest confidentiality
                # rule as the question: never persist the plaintext).
                option_encrypted = None
                stored_option = option_text
                if self._encrypt_polls:
                    from src.utils.encryption import encrypt_data

                    option_encrypted = encrypt_data(option_text)
                    stored_option = ""
                self._db.execute(
                    """INSERT INTO poll_options (id, poll_id, text, text_encrypted, position)
                       VALUES (?, ?, ?, ?, ?)""",
                    (option_id, poll_id, stored_option, option_encrypted, i),
                    auto_commit=False,
                )

            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        logger.debug(f"Created poll {poll_id} on message {message_id}")

        result = self.get_poll(poll_id, user_id)
        if result is None:
            raise PollError("Failed to create poll: poll not found after insert")
        return result

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

        # One vote per user per poll.  The pre-check is a fast path; the
        # authoritative guard is the ``UNIQUE(poll_id, user_id)`` constraint
        # on ``poll_voters``, inserted in the SAME transaction so two racing
        # requests cannot both pass the check and both commit.  ``change_vote``
        # is the dedicated endpoint for users who want to revise a selection.
        if self._has_voted(user_id, poll_id):
            raise AlreadyVotedError(
                "User has already voted on this poll; "
                "call change_vote() to revise the selection"
            )

        now = self._get_timestamp()

        try:
            self._db.begin_transaction()

            self._db.execute(
                """INSERT INTO poll_voters (poll_id, user_id, voted_at)
                   VALUES (?, ?, ?)""",
                (poll_id, user_id, now),
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
        except Exception as exc:
            self._db.rollback()
            # A UNIQUE violation on ``poll_voters`` (poll_id, user_id) means
            # a concurrent request already claimed this voter slot.
            if _is_unique_violation(exc):
                raise AlreadyVotedError("User has already voted on this poll") from exc
            raise

        logger.debug(f"User {user_id} voted on poll {poll_id}")

        self._emit_poll_update(poll, user_id, "vote")

        return self.get_results(poll_id, user_id)

    def change_vote(
        self, user_id: SnowflakeID, poll_id: SnowflakeID, option_ids: List[SnowflakeID]
    ) -> PollResults:
        """
        Revise a user's existing vote on a poll (replace previous selection).

        The ``poll_voters`` registration row is preserved (the user has
        still voted); only the per-option ``poll_votes`` rows are replaced
        atomically.

        Raises:
            PollNotFoundError: Poll not found
            PollEndedError: Poll has been closed/expired
            PollOptionNotFoundError: Option not valid for this poll
            MultipleVoteNotAllowedError: Multiple options on single-choice poll
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

        unique_option_ids = list(set(option_ids))
        if not poll.allow_multiple_choice and len(unique_option_ids) > 1:
            raise MultipleVoteNotAllowedError(
                "This poll does not allow multiple choice voting"
            )

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

        try:
            self._db.begin_transaction()

            # Replace the user's previous per-option selections.
            self._db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )

            # Keep the voter registration row in sync (a user revising a
            # vote is still a voter); insert only when absent so the
            # UNIQUE(poll_id, user_id) constraint is never hit.
            existing_voter = self._db.fetch_one(
                "SELECT 1 FROM poll_voters WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
            )
            if not existing_voter:
                self._db.execute(
                    """INSERT INTO poll_voters (poll_id, user_id, voted_at)
                       VALUES (?, ?, ?)""",
                    (poll_id, user_id, now),
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
        except Exception as exc:
            self._db.rollback()
            if _is_unique_violation(exc):
                raise AlreadyVotedError(
                    "User already voted; call change_vote() to revise the selection"
                ) from exc
            raise

        logger.debug(f"User {user_id} changed vote on poll {poll_id}")

        self._emit_poll_update(poll, user_id, "change")

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
        can_see_results = self._can_view_results(poll, user_voted)

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

        # Total votes = number of unique participants.  This aggregate is
        # just as privacy-sensitive as the per-option counts, so it is only
        # reported when the caller is entitled to see the results.
        total_voters = self._voter_count(poll_id) if can_see_results else None

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

        self._emit_poll_update(poll, user_id, "close")

        result = self.get_poll(poll_id, user_id)
        if result is None:
            raise PollError("Failed to fetch poll after closing")
        return result

    def has_voted(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """Return True if `user_id` has cast at least one vote on `poll_id`.

        This is a cheap existence check — it returns False for polls that
        don't exist, which keeps callers from having to first verify the
        poll themselves.
        """
        if not poll_id or not user_id:
            return False
        return self._has_voted(user_id, poll_id)

    def remove_vote(self, user_id: SnowflakeID, poll_id: SnowflakeID) -> bool:
        """Remove every vote `user_id` previously cast on `poll_id`.

        Returns True when at least one row was deleted.  Raises
        :class:`PollNotFoundError` if the poll itself does not exist so
        callers can distinguish "user hadn't voted" from "no such poll",
        and :class:`PollEndedError` once the poll has ended (consistent
        with :meth:`vote` — recorded outcomes of closed polls must not
        be altered after the fact).
        """
        poll = self.get_poll(poll_id, user_id)
        if not poll:
            raise PollNotFoundError("Poll not found")

        if poll.is_ended or (poll.ends_at and self._get_timestamp() >= poll.ends_at):
            if not poll.is_ended:
                self._end_poll(poll_id)
            raise PollEndedError("Poll has ended")

        # Both deletes run in ONE transaction: a crash between them must
        # never leave the user de-voted but still registered as a voter
        # (which would block re-voting) or vice versa.
        deleted = 0
        try:
            self._db.begin_transaction()
            cursor = self._db.execute(
                "DELETE FROM poll_votes WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )
            try:
                deleted = cursor.rowcount
            except Exception:
                # Database drivers without rowcount support still delete.
                deleted = 0
            try:
                cursor.close()
            except Exception:
                pass

            # De-register the voter slot so the user can vote again on this poll.
            self._db.execute(
                "DELETE FROM poll_voters WHERE poll_id = ? AND user_id = ?",
                (poll_id, user_id),
                auto_commit=False,
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise

        logger.debug(
            "User %s vote(s) on poll %s cleared (%d row(s))",
            user_id,
            poll_id,
            deleted,
        )

        self._emit_poll_update(poll, user_id, "remove")

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
            raise PermissionDeniedError(
                "Only the poll creator (or a moderator) can view the voter roster"
            )

        if poll.results_visibility == PollResultsVisibility.AFTER_VOTE:
            return []

        if poll.results_visibility == PollResultsVisibility.AFTER_END:
            if not poll.is_ended and not (
                poll.ends_at and self._get_timestamp() >= poll.ends_at
            ):
                return []

        rows = self._db.fetch_all(
            "SELECT user_id FROM poll_voters WHERE poll_id = ? ORDER BY user_id",
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
        self._db.execute("DELETE FROM poll_voters WHERE poll_id = ?", (poll_id,))
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
