"""Comprehensive Polls tests targeting 80%+ coverage."""

import pytest
from src.core.polls.exceptions import (
    InvalidPollQuestionError,
    InvalidPollOptionError,
    PollOptionLimitError,
    InvalidPollDurationError,
    PollEndedError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
    PollOptionNotFoundError,
    PermissionDeniedError,
)
from src.core.polls.models import PollResultsVisibility


# Each test that needs a poll-creator + DM + message just asks for
# ``user_with_dm`` and unpacks ``user``, ``_friend``, ``dm``, ``msg``.


class TestPollErrors:
    def test_invalid_question_empty(self, poll_manager):
        """Poll question cannot be empty."""
        with pytest.raises(InvalidPollQuestionError):
            poll_manager._validate_question("")

    def test_invalid_question_too_long(self, poll_manager):
        """Poll question too long."""
        with pytest.raises(InvalidPollQuestionError):
            poll_manager._validate_question("x" * 500)

    def test_invalid_option_empty(self, poll_manager):
        """Poll option cannot be empty."""
        with pytest.raises(InvalidPollOptionError):
            poll_manager._validate_option("")

    def test_too_few_options(self, poll_manager, user_with_dm):
        """Need minimum number of options."""
        user, _friend, _dm, msg = user_with_dm
        with pytest.raises(PollOptionLimitError):
            poll_manager.create_poll(user.id, msg.id, "Question?", ["Option 1"])

    def test_too_many_options(self, poll_manager, user_with_dm, monkeypatch):
        """Cannot exceed max options."""
        user, _friend, _dm, msg = user_with_dm
        monkeypatch.setitem(poll_manager._config, "max_options", 3)
        with pytest.raises(PollOptionLimitError):
            poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B", "C", "D"])

    def test_invalid_duration(self, poll_manager):
        """Invalid poll duration."""
        with pytest.raises(InvalidPollDurationError):
            poll_manager._validate_duration(0)
        with pytest.raises(InvalidPollDurationError):
            poll_manager._validate_duration(1000)

    def test_vote_ended_poll(self, poll_manager, user_with_dm):
        """Cannot vote on ended poll."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], duration_hours=1
        )
        poll_manager._db.execute(
            "UPDATE poll_polls SET ends_at = ? WHERE id = ?", (1, poll.id)
        )
        with pytest.raises(PollEndedError):
            poll_manager.vote(user.id, poll.id, [poll.options[0].id])

    def test_vote_already_voted(self, poll_manager, user_with_dm):
        """Cannot vote twice."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        with pytest.raises(AlreadyVotedError):
            poll_manager.vote(user.id, poll.id, [poll.options[1].id])

    def test_multiple_choice_not_allowed(self, poll_manager, user_with_dm):
        """Cannot vote multiple when not allowed."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id,
            msg.id,
            "Question?",
            ["A", "B"],
            allow_multiple_choice=False,
        )
        with pytest.raises(MultipleVoteNotAllowedError):
            poll_manager.vote(
                user.id, poll.id, [poll.options[0].id, poll.options[1].id]
            )

    def test_end_poll_early(self, poll_manager, user_with_dm):
        """Can end poll early."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], duration_hours=24
        )
        ended = poll_manager.close_poll(user.id, poll.id)
        assert ended.is_ended

    def test_get_poll_results(self, poll_manager, user_with_dm):
        """Get poll results."""
        user, friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        poll_manager.vote(friend.id, poll.id, [poll.options[1].id])
        results = poll_manager.get_results(poll.id, user.id)
        assert len(results.options) >= 2

    def test_remove_vote(self, poll_manager, user_with_dm):
        """Can remove vote."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        poll_manager.remove_vote(user.id, poll.id)
        assert poll_manager.has_voted(user.id, poll.id) is False


class TestPollCreation:
    """Test poll creation."""

    def test_create_basic_poll(self, poll_manager, user_with_dm):
        """Create basic poll."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id, msg.id, "Favorite color?", ["Red", "Blue", "Green"]
        )
        assert poll.question == "Favorite color?"
        assert len(poll.options) == 3

    def test_create_poll_with_duration(self, poll_manager, user_with_dm):
        """Create poll with duration."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], duration_hours=24
        )
        assert poll.ends_at is not None

    def test_create_multiple_choice_poll(self, poll_manager, user_with_dm):
        """Create multiple choice poll."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id,
            msg.id,
            "Question?",
            ["A", "B"],
            allow_multiple_choice=True,
        )
        assert poll.allow_multiple_choice is True

    def test_create_anonymous_poll(self, poll_manager, user_with_dm):
        """Create anonymous poll."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id,
            msg.id,
            "Question?",
            ["A", "B"],
            results_visibility=PollResultsVisibility.AFTER_VOTE,
        )
        assert poll.results_visibility == PollResultsVisibility.AFTER_VOTE


class TestPollVoting:
    """Test poll voting."""

    def test_vote_single_choice(self, poll_manager, user_with_dm):
        """Vote single choice."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        assert poll_manager.has_voted(user.id, poll.id) is True

    def test_vote_multiple_choice(self, poll_manager, user_with_dm):
        """Vote multiple choice."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id,
            msg.id,
            "Question?",
            ["A", "B", "C"],
            allow_multiple_choice=True,
        )
        poll_manager.vote(user.id, poll.id, [poll.options[0].id, poll.options[1].id])
        assert poll_manager.has_voted(user.id, poll.id) is True

    def test_change_vote(self, poll_manager, user_with_dm):
        """Change vote.

        ``polls.vote()`` raises ``AlreadyVotedError`` on a second call;
        ``polls.change_vote()`` is the canonical swap-style API.
        """
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        poll_manager.remove_vote(user.id, poll.id)
        poll_manager.vote(user.id, poll.id, [poll.options[1].id])

        assert poll_manager.has_voted(user.id, poll.id) is True

    def test_vote_invalid_option(self, poll_manager, user_with_dm):
        """Vote invalid option."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        with pytest.raises(PollOptionNotFoundError):
            poll_manager.vote(user.id, poll.id, [999])


class TestPollResults:
    """Test poll results."""

    def test_get_detailed_results(self, poll_manager, user_with_dm):
        """Get detailed results."""
        user, friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        poll_manager.vote(friend.id, poll.id, [poll.options[1].id])
        results = poll_manager.get_results(poll.id, user.id)
        assert len(results.options) == 2

    def test_get_voters_anonymous_poll(self, poll_manager, user_with_dm):
        """Get voters for anonymous poll."""
        user, friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id,
            msg.id,
            "Question?",
            ["A", "B"],
            results_visibility=PollResultsVisibility.AFTER_VOTE,
        )
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        voters = poll_manager.get_voters(poll.id, user.id)
        # Anonymous polls should not show voters
        assert len(voters) == 0

    def test_get_voters_public_poll(self, poll_manager, user_with_dm):
        """Get voters for public poll."""
        user, friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(
            user.id,
            msg.id,
            "Question?",
            ["A", "B"],
            results_visibility=PollResultsVisibility.ALWAYS,
        )
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        voters = poll_manager.get_voters(poll.id, user.id)
        assert len(voters) >= 1


class TestPollManagement:
    """Test poll management."""

    def test_end_poll_not_creator(self, poll_manager, user_with_dm):
        """Non-creator cannot end poll."""
        user, friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        with pytest.raises(PermissionDeniedError):
            poll_manager.close_poll(friend.id, poll.id)

    def test_delete_poll(self, poll_manager, user_with_dm):
        """Delete poll."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        result = poll_manager.delete_poll(user.id, poll.id)
        assert result is True

    def test_delete_poll_not_creator(self, poll_manager, user_with_dm):
        """Non-creator cannot delete poll."""
        user, friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        with pytest.raises(PermissionDeniedError):
            poll_manager.delete_poll(friend.id, poll.id)

    def test_get_poll_not_found(self, poll_manager):
        """Get nonexistent poll."""
        poll = poll_manager.get_poll(999, 1)
        assert poll is None

    def test_get_user_votes(self, poll_manager, user_with_dm):
        """Get user votes."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        votes = poll_manager.get_user_votes(user.id)
        assert len(votes) >= 1

    def test_has_voted(self, poll_manager, user_with_dm):
        """Check if user voted."""
        user, _friend, _dm, msg = user_with_dm
        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        assert poll_manager.has_voted(user.id, poll.id) is False
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        assert poll_manager.has_voted(user.id, poll.id) is True
