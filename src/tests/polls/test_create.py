"""
Tests for poll creation.
"""

import pytest
from src.core.polls import (
    InvalidPollQuestionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    PollResultsVisibility,
)


class TestCreatePoll:
    """Tests for creating polls."""

    def test_create_poll_success(self, dm_with_message):
        """Test creating a poll successfully."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="What is your favorite color?",
            options=["Red", "Blue", "Green"],
        )

        assert poll is not None
        assert poll.question == "What is your favorite color?"
        assert len(poll.options) == 3
        assert poll.created_by == user1.id
        assert poll.message_id == msg.id
        assert poll.is_ended is False

    def test_create_poll_with_duration(self, dm_with_message):
        """Test creating a poll with duration."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Duration poll")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=new_msg.id,
            question="Time-limited poll?",
            options=["Yes", "No"],
            duration_hours=24,
        )

        assert poll.ends_at is not None
        assert poll.ends_at > poll.created_at

    def test_create_poll_multiple_choice(self, dm_with_message):
        """Test creating a multiple choice poll."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Multi poll")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=new_msg.id,
            question="Select all that apply",
            options=["A", "B", "C"],
            allow_multiple_choice=True,
        )

        assert poll.allow_multiple_choice is True

    def test_create_poll_hidden_results(self, dm_with_message):
        """Test creating a poll with hidden results."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Hidden poll")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=new_msg.id,
            question="Secret poll?",
            options=["Yes", "No"],
            results_visibility=PollResultsVisibility.AFTER_END,
        )

        assert poll.results_visibility == PollResultsVisibility.AFTER_END

    def test_create_poll_empty_question_fails(self, dm_with_message):
        """Test creating poll with empty question fails."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Empty question")

        with pytest.raises(InvalidPollQuestionError):
            polls.create_poll(
                user_id=user1.id,
                message_id=new_msg.id,
                question="",
                options=["Yes", "No"],
            )

    def test_create_poll_too_few_options_fails(self, dm_with_message):
        """Test creating poll with too few options fails."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Few options")

        with pytest.raises(PollOptionLimitError) as exc_info:
            polls.create_poll(
                user_id=user1.id,
                message_id=new_msg.id,
                question="Only one option?",
                options=["Only"],
            )

        assert exc_info.value.min_options == 2

    def test_create_poll_too_many_options_fails(self, dm_with_message):
        """Test creating poll with too many options fails."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Many options")

        with pytest.raises(PollOptionLimitError) as exc_info:
            polls.create_poll(
                user_id=user1.id,
                message_id=new_msg.id,
                question="Too many options?",
                options=[f"Option {i}" for i in range(15)],
            )

        assert exc_info.value.max_options == 10

    def test_create_poll_invalid_duration_fails(self, dm_with_message):
        """Test creating poll with invalid duration fails."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        new_msg = messaging.send_message(user1.id, dm.id, "Bad duration")

        with pytest.raises(InvalidPollDurationError):
            polls.create_poll(
                user_id=user1.id,
                message_id=new_msg.id,
                question="Invalid duration?",
                options=["Yes", "No"],
                duration_hours=1000,
            )


class TestGetPoll:
    """Tests for getting polls."""

    def test_get_poll_success(self, poll_with_options):
        """Test getting a poll successfully."""
        user1, user2, poll, polls, messaging = poll_with_options

        retrieved = polls.get_poll(poll.id, user1.id)

        assert retrieved is not None
        assert retrieved.id == poll.id
        assert retrieved.question == poll.question

    def test_get_poll_nonexistent(self, dm_with_message):
        """Test getting nonexistent poll returns None."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        result = polls.get_poll(999999999, user1.id)
        assert result is None
