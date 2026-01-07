"""
Tests for poll voting.
"""

import pytest
import uuid
from src.core.polls import (
    PollNotFoundError,
    PollOptionNotFoundError,
    AlreadyVotedError,
    MultipleVoteNotAllowedError,
)


class TestVoting:
    """Tests for voting on polls."""

    def test_vote_success(self, poll_with_options):
        """Test voting on a poll successfully."""
        user1, user2, poll, polls, messaging = poll_with_options

        option_id = poll.options[0].id
        results = polls.vote(user2.id, poll.id, [option_id])

        assert results is not None
        assert results.user_voted is True
        assert option_id in results.user_votes

    def test_vote_updates_count(self, poll_with_options):
        """Test voting updates vote count."""
        user1, user2, poll, polls, messaging = poll_with_options

        option_id = poll.options[1].id
        results = polls.vote(user1.id, poll.id, [option_id])

        voted_option = next(o for o in results.options if o.id == option_id)
        assert voted_option.vote_count >= 1

    def test_vote_already_voted_fails(self, modules):
        """Test voting twice fails."""
        auth, messaging, polls = modules.auth, modules.messaging, modules.polls

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"double_vote1_{unique_id}",
            email=f"double_vote1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"double_vote2_{unique_id}",
            email=f"double_vote2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Double vote test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Double vote?",
            options=["A", "B"],
        )

        polls.vote(user2.id, poll.id, [poll.options[0].id])

        with pytest.raises(AlreadyVotedError):
            polls.vote(user2.id, poll.id, [poll.options[1].id])

    def test_vote_multiple_choice(self, modules):
        """Test multiple choice voting."""
        auth, messaging, polls = modules.auth, modules.messaging, modules.polls

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"multi_vote1_{unique_id}",
            email=f"multi_vote1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"multi_vote2_{unique_id}",
            email=f"multi_vote2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Multi choice test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Select multiple",
            options=["A", "B", "C"],
            allow_multiple_choice=True,
        )

        option_ids = [poll.options[0].id, poll.options[2].id]
        results = polls.vote(user2.id, poll.id, option_ids)

        assert len(results.user_votes) == 2
        assert poll.options[0].id in results.user_votes
        assert poll.options[2].id in results.user_votes

    def test_vote_multiple_not_allowed_fails(self, modules):
        """Test multiple votes on single choice poll fails."""
        auth, messaging, polls = modules.auth, modules.messaging, modules.polls

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"single_vote1_{unique_id}",
            email=f"single_vote1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"single_vote2_{unique_id}",
            email=f"single_vote2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Single choice test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Single choice only",
            options=["A", "B", "C"],
            allow_multiple_choice=False,
        )

        with pytest.raises(MultipleVoteNotAllowedError):
            polls.vote(user2.id, poll.id, [poll.options[0].id, poll.options[1].id])

    def test_vote_invalid_option_fails(self, poll_with_options):
        """Test voting for invalid option fails."""
        user1, user2, poll, polls, messaging = poll_with_options

        with pytest.raises(PollOptionNotFoundError):
            polls.vote(user2.id, poll.id, [999999999])

    def test_vote_nonexistent_poll_fails(self, dm_with_message):
        """Test voting on nonexistent poll fails."""
        user1, user2, dm, msg, polls, messaging = dm_with_message

        with pytest.raises(PollNotFoundError):
            polls.vote(user1.id, 999999999, [1])
