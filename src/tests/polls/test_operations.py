"""Comprehensive Polls tests targeting 80%+ coverage."""

import pytest
from unittest.mock import patch
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

    def test_too_few_options(self, poll_manager, auth_manager, messaging_manager):
        """Need minimum number of options."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        with pytest.raises(PollOptionLimitError):
            poll_manager.create_poll(user.id, msg.id, "Question?", ["Option 1"])

    def test_too_many_options(
        self, poll_manager, auth_manager, messaging_manager, monkeypatch
    ):
        """Cannot exceed max options."""
        from src.utils import encryption

        monkeypatch.setitem(poll_manager._config, "max_options", 3)

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        with pytest.raises(PollOptionLimitError):
            poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B", "C", "D"])

    def test_invalid_duration(self, poll_manager):
        """Invalid poll duration."""
        with pytest.raises(InvalidPollDurationError):
            poll_manager._validate_duration(0)

        with pytest.raises(InvalidPollDurationError):
            poll_manager._validate_duration(1000)

    def test_vote_ended_poll(self, poll_manager, auth_manager, messaging_manager):
        """Cannot vote on ended poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], duration_hours=1
        )

        poll_manager._db.execute(
            "UPDATE poll_polls SET ends_at = ? WHERE id = ?", (1, poll.id)
        )

        with pytest.raises(PollEndedError):
            poll_manager.vote(user.id, poll.id, [poll.options[0].id])

    def test_vote_already_voted(self, poll_manager, auth_manager, messaging_manager):
        """Cannot vote twice."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])

        with pytest.raises(AlreadyVotedError):
            poll_manager.vote(user.id, poll.id, [poll.options[1].id])

    def test_multiple_choice_not_allowed(
        self, poll_manager, auth_manager, messaging_manager
    ):
        """Cannot vote multiple when not allowed."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], allow_multiple_choice=False
        )

        with pytest.raises(MultipleVoteNotAllowedError):
            poll_manager.vote(
                user.id, poll.id, [poll.options[0].id, poll.options[1].id]
            )

    def test_end_poll_early(self, poll_manager, auth_manager, messaging_manager):
        """Can end poll early."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], duration_hours=24
        )

        ended = poll_manager.close_poll(user.id, poll.id)
        assert ended.is_ended

    def test_get_poll_results(self, poll_manager, auth_manager, messaging_manager):
        """Get poll results."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "testuser1", "test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "testuser2", "test2@example.com", "TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "test")

        poll = poll_manager.create_poll(user1.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user1.id, poll.id, [poll.options[0].id])
        poll_manager.vote(user2.id, poll.id, [poll.options[1].id])

        results = poll_manager.get_results(poll.id, user1.id)
        assert len(results.options) >= 2

    def test_remove_vote(self, poll_manager, auth_manager, messaging_manager):
        """Can remove vote."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])

        poll_manager.remove_vote(user.id, poll.id)
        # Verify vote removed
        assert poll_manager.has_voted(user.id, poll.id) is False


class TestPollCreation:
    """Test poll creation."""

    def test_create_basic_poll(self, poll_manager, auth_manager, messaging_manager):
        """Create basic poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Favorite color?", ["Red", "Blue", "Green"]
        )

        assert poll.question == "Favorite color?"
        assert len(poll.options) == 3

    def test_create_poll_with_duration(
        self, poll_manager, auth_manager, messaging_manager
    ):
        """Create poll with duration."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], duration_hours=24
        )

        assert poll.ends_at is not None
        assert poll.ends_at is not None

    def test_create_multiple_choice_poll(
        self, poll_manager, auth_manager, messaging_manager
    ):
        """Create multiple choice poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B"], allow_multiple_choice=True
        )

        assert poll.allow_multiple_choice is True

    def test_create_anonymous_poll(self, poll_manager, auth_manager, messaging_manager):
        """Create anonymous poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

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

    def test_vote_single_choice(self, poll_manager, auth_manager, messaging_manager):
        """Vote single choice."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])

        assert poll_manager.has_voted(user.id, poll.id) is True

    def test_vote_multiple_choice(self, poll_manager, auth_manager, messaging_manager):
        """Vote multiple choice."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user.id, msg.id, "Question?", ["A", "B", "C"], allow_multiple_choice=True
        )
        poll_manager.vote(user.id, poll.id, [poll.options[0].id, poll.options[1].id])

        assert poll_manager.has_voted(user.id, poll.id) is True

    def test_change_vote(self, poll_manager, auth_manager, messaging_manager):
        """Change vote."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        poll_manager.vote(user.id, poll.id, [poll.options[1].id])

        assert poll_manager.has_voted(user.id, poll.id) is True

    def test_vote_invalid_option(self, poll_manager, auth_manager, messaging_manager):
        """Vote invalid option."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])

        with pytest.raises(PollOptionNotFoundError):
            poll_manager.vote(user.id, poll.id, [999])


class TestPollResults:
    """Test poll results."""

    def test_get_detailed_results(self, poll_manager, auth_manager, messaging_manager):
        """Get detailed results."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "testuser1", "test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "testuser2", "test2@example.com", "TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "test")

        poll = poll_manager.create_poll(user1.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user1.id, poll.id, [poll.options[0].id])
        poll_manager.vote(user2.id, poll.id, [poll.options[1].id])

        results = poll_manager.get_results(poll.id, user1.id)
        assert len(results.options) == 2

    def test_get_voters_anonymous_poll(
        self, poll_manager, auth_manager, messaging_manager
    ):
        """Get voters for anonymous poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "testuser1", "test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "testuser2", "test2@example.com", "TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user1.id,
            msg.id,
            "Question?",
            ["A", "B"],
            results_visibility=PollResultsVisibility.AFTER_VOTE,
        )
        poll_manager.vote(user1.id, poll.id, [poll.options[0].id])

        voters = poll_manager.get_voters(poll.id, user1.id)
        # Anonymous polls should not show voters
        assert len(voters) == 0

    def test_get_voters_public_poll(
        self, poll_manager, auth_manager, messaging_manager
    ):
        """Get voters for public poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "testuser1", "test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "testuser2", "test2@example.com", "TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "test")

        poll = poll_manager.create_poll(
            user1.id,
            msg.id,
            "Question?",
            ["A", "B"],
            results_visibility=PollResultsVisibility.ALWAYS,
        )
        poll_manager.vote(user1.id, poll.id, [poll.options[0].id])

        voters = poll_manager.get_voters(poll.id, user1.id)
        assert len(voters) >= 1


class TestPollManagement:
    """Test poll management."""

    def test_end_poll_not_creator(self, poll_manager, auth_manager, messaging_manager):
        """Non-creator cannot end poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "testuser1", "test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "testuser2", "test2@example.com", "TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "test")

        poll = poll_manager.create_poll(user1.id, msg.id, "Question?", ["A", "B"])

        with pytest.raises(PermissionDeniedError):
            poll_manager.close_poll(user2.id, poll.id)

    def test_delete_poll(self, poll_manager, auth_manager, messaging_manager):
        """Delete poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        result = poll_manager.delete_poll(user.id, poll.id)

        assert result is True

    def test_delete_poll_not_creator(
        self, poll_manager, auth_manager, messaging_manager
    ):
        """Non-creator cannot delete poll."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                "testuser1", "test1@example.com", "TestPass123!"
            )
            user2 = auth_manager.register(
                "testuser2", "test2@example.com", "TestPass123!"
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "test")

        poll = poll_manager.create_poll(user1.id, msg.id, "Question?", ["A", "B"])

        with pytest.raises(PermissionDeniedError):
            poll_manager.delete_poll(user2.id, poll.id)

    def test_get_poll_not_found(self, poll_manager):
        """Get nonexistent poll."""
        poll = poll_manager.get_poll(999, 1)
        assert poll is None

    def test_get_user_votes(self, poll_manager, auth_manager, messaging_manager):
        """Get user votes."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
        poll_manager.vote(user.id, poll.id, [poll.options[0].id])

        votes = poll_manager.get_user_votes(user.id)
        assert len(votes) >= 1

    def test_has_voted(self, poll_manager, auth_manager, messaging_manager):
        """Check if user voted."""
        from src.utils import encryption

        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register("testuser", "test@example.com", "TestPass123!")

        dm = messaging_manager.create_dm(user.id, user.id)
        msg = messaging_manager.send_message(user.id, dm.id, "test")

        poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])

        assert poll_manager.has_voted(user.id, poll.id) is False

        poll_manager.vote(user.id, poll.id, [poll.options[0].id])

        assert poll_manager.has_voted(user.id, poll.id) is True
