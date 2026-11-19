"""Poll creation and validation."""

from typing import List, Optional

import utils.logger as logger
from src.core.security.hardening import HardeningViolation, bounded_int
from ...base import SnowflakeID

from ..exceptions import (
    InvalidPollDurationError,
    MessageNotFoundError,
    PermissionDeniedError,
    PollAlreadyExistsError,
    PollError,
)
from ..limits import (
    bound_option_count,
    bound_timestamp,
    resolve_field_limits,
    validate_option_text,
    validate_question_text,
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

        bound_option_count(options, self._config)

        validated_options = [self._validate_option(opt) for opt in options]
        duration_hours = self._validate_duration(duration_hours)

        now = bound_timestamp(self._get_timestamp(), "poll created_at")
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
        # Creation is a mutation boundary: leave no memoized read behind so the
        # next read observes the inserted row directly (never a stale body).
        self._invalidate_poll_read_cache()
        return result

    def _validate_question(self, question: str) -> str:
        """Validate and sanitize poll question."""
        return validate_question_text(question, self._config)

    def _validate_option(self, option: str) -> str:
        """Validate and sanitize poll option."""
        return validate_option_text(option, self._config)

    def _validate_duration(self, duration_hours: Optional[int]) -> Optional[int]:
        """Validate poll duration."""
        if duration_hours is None:
            return None

        limits = resolve_field_limits(self._config)
        min_hours = limits.min_duration_hours
        max_hours = limits.max_duration_hours
        if isinstance(duration_hours, bool) or not isinstance(duration_hours, int):
            raise InvalidPollDurationError(
                f"Poll duration must be between {min_hours} and {max_hours} hours",
                min_hours,
                max_hours,
            )
        try:
            return bounded_int(
                duration_hours,
                "duration_hours",
                minimum=min_hours,
                maximum=max_hours,
            )
        except HardeningViolation as exc:
            raise InvalidPollDurationError(
                f"Poll duration must be between {min_hours} and {max_hours} hours",
                min_hours,
                max_hours,
            ) from exc
