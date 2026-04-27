"""
Tests for poll results.
"""

import uuid
from src.core.polls import PollResultsVisibility


class TestPollResults:
    """Tests for poll results."""

    def test_get_results_success(self, poll_with_options, dm_with_message):
        """Test getting poll results successfully."""
        poll = poll_with_options
        user1, user2, dm, msg, polls, messaging = dm_with_message

        results = polls.get_results(poll.id, user1.id)

        assert results is not None
        assert results.poll.id == poll.id
        assert len(results.options) == len(poll.options)

    def test_results_show_vote_counts(
        self, auth_manager, messaging_manager, poll_manager
    ):
        """Test results show correct vote counts."""
        unique_id = uuid.uuid4().hex[:8]
        users = []
        for i in range(4):
            user = auth_manager.register(
                username=f"count_user{i}_{unique_id}",
                email=f"count_user{i}_{unique_id}@example.com",
                password="TestPass123!",
            )
            users.append(user)

        dm = messaging_manager.create_dm(users[0].id, users[1].id)
        msg = messaging_manager.send_message(users[0].id, dm.id, "Count test")

        poll = poll_manager.create_poll(
            user_id=users[0].id,
            message_id=msg.id,
            question="Count test?",
            options=["A", "B"],
        )

        poll_manager.vote(users[1].id, poll.id, [poll.options[0].id])

        results = poll_manager.get_results(poll.id, users[0].id)

        option_a = next(o for o in results.options if o.text == "A")
        assert option_a.vote_count == 1

    def test_results_visibility_after_vote(
        self, auth_manager, messaging_manager, poll_manager
    ):
        """Test results hidden until user votes."""
        unique_id = uuid.uuid4().hex[:8]
        user1 = auth_manager.register(
            username=f"vis_user1_{unique_id}",
            email=f"vis_user1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"vis_user2_{unique_id}",
            email=f"vis_user2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Visibility test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Hidden until vote?",
            options=["A", "B"],
            results_visibility=PollResultsVisibility.AFTER_VOTE,
        )

        poll_manager.vote(user1.id, poll.id, [poll.options[0].id])

        results_before = poll_manager.get_results(poll.id, user2.id)
        assert results_before.user_voted is False

        poll_manager.vote(user2.id, poll.id, [poll.options[1].id])

        results_after = poll_manager.get_results(poll.id, user2.id)
        assert results_after.user_voted is True

    def test_results_total_votes(self, auth_manager, messaging_manager, poll_manager):
        """Test total votes calculation."""
        unique_id = uuid.uuid4().hex[:8]
        user1 = auth_manager.register(
            username=f"total_user1_{unique_id}",
            email=f"total_user1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"total_user2_{unique_id}",
            email=f"total_user2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Total test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Total votes?",
            options=["A", "B"],
        )

        poll_manager.vote(user1.id, poll.id, [poll.options[0].id])
        poll_manager.vote(user2.id, poll.id, [poll.options[1].id])

        results = poll_manager.get_results(poll.id, user1.id)
        assert results.total_votes == 2

    def test_results_user_votes_tracked(self, poll_with_options, dm_with_message):
        """Test user's votes are tracked in results."""
        poll = poll_with_options
        user1, user2, dm, msg, polls, messaging = dm_with_message

        option_id = poll.options[0].id
        polls.vote(user2.id, poll.id, [option_id])

        results = polls.get_results(poll.id, user2.id)

        assert results.user_voted is True
        assert option_id in results.user_votes
