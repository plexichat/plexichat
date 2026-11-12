"""Poll creation and validation."""

from typing import List, Optional

import utils.logger as logger
from ...base import SnowflakeID

from ..exceptions import (
    InvalidPollDurationError,
    InvalidPollOptionError,
    InvalidPollQuestionError,
    MessageNotFoundError,
    PermissionDeniedError,
    PollAlreadyExistsError,
    PollError,
    PollOptionLimitError,
)
from ..models import Poll, PollResultsVisibility
from .base import PollManagerBase, _is_unique_violation


class CreationMixin(PollManagerBase):
    """Create polls with validated question, options, and duration."""

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
            PollAlreadyExistsError: Message already carries a poll
        """
        messaging = self._require_messaging()
        msg = messaging.get_message(user_id, message_id)
        if not msg:
            raise MessageNotFoundError("Message not found")
        if msg.author_id != user_id:
            raise PermissionDeniedError("Can only create polls on own messages")

        # ``poll_polls.message_id`` is UNIQUE -- a message may carry at
        # most one poll.  Report that as a domain error up front so the
        # route layer can answer 409 instead of letting the raw driver
        # IntegrityError surface as a 500.  The UNIQUE violation raised
        # by a poll that lands between this check and our INSERT is
        # translated to the same error below.
        existing = self._db.fetch_one(
            "SELECT id FROM poll_polls WHERE message_id = ?", (message_id,)
        )
        if existing:
            raise PollAlreadyExistsError(
                f"Message {message_id} already has a poll attached"
            )

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
        except Exception as exc:
            self._db.rollback()
            # A concurrent create for the same message won the race
            # between our existence check and this INSERT; the UNIQUE
            # constraint is the authoritative guard, so map it onto the
            # same domain error the pre-check raises.
            if _is_unique_violation(exc):
                raise PollAlreadyExistsError(
                    f"Message {message_id} already has a poll attached"
                ) from exc
            raise

        logger.debug(f"Created poll {poll_id} on message {message_id}")

        result = self.get_poll(poll_id, user_id)
        if result is None:
            raise PollError("Failed to create poll: poll not found after insert")
        return result

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
