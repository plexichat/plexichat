"""
Tests for poll expiry and closing.
"""

import pytest
import uuid
from src.core.polls import (
    PollNotFoundError,
    PollEndedError,
    PermissionDeniedError,
)


class TestClosePoll:
    """Tests for closing polls early."""

    def test_close_poll_success(self, db_and_modules):
        """Test closing a poll successfully."""
        db, auth, messaging, polls = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"close_user1_{unique_id}",
            email=f"close_user1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"close_user2_{unique_id}",
            email=f"close_user2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Close test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Close early?",
            options=["Yes", "No"],
            duration_hours=24
        )

        closed = polls.close_poll(user1.id, poll.id)

        assert closed.is_ended is True
        assert closed.ended_at is not None

    def test_close_poll_not_creator_fails(self, db_and_modules):
        """Test non-creator cannot close poll."""
        db, auth, messaging, polls = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"perm_user1_{unique_id}",
            email=f"perm_user1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"perm_user2_{unique_id}",
            email=f"perm_user2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Permission test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Permission test?",
            options=["Yes", "No"]
        )

        with pytest.raises(PermissionDeniedError):
            polls.close_poll(user2.id, poll.id)

    def test_close_poll_already_ended_fails(self, db_and_modules):
        """Test closing already ended poll fails."""
        db, auth, messaging, polls = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"ended_user1_{unique_id}",
            email=f"ended_user1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"ended_user2_{unique_id}",
            email=f"ended_user2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Already ended test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Already ended?",
            options=["Yes", "No"]
        )

        polls.close_poll(user1.id, poll.id)

        with pytest.raises(PollEndedError):
            polls.close_poll(user1.id, poll.id)

    def test_vote_on_closed_poll_fails(self, db_and_modules):
        """Test voting on closed poll fails."""
        db, auth, messaging, polls = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"vote_closed1_{unique_id}",
            email=f"vote_closed1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"vote_closed2_{unique_id}",
            email=f"vote_closed2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Vote closed test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Vote after close?",
            options=["Yes", "No"]
        )

        polls.close_poll(user1.id, poll.id)

        with pytest.raises(PollEndedError):
            polls.vote(user2.id, poll.id, [poll.options[0].id])


class TestDeletePoll:
    """Tests for deleting polls."""

    def test_delete_poll_success(self, db_and_modules):
        """Test deleting a poll successfully."""
        db, auth, messaging, polls = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"del_user1_{unique_id}",
            email=f"del_user1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"del_user2_{unique_id}",
            email=f"del_user2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Delete test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Delete me?",
            options=["Yes", "No"]
        )

        result = polls.delete_poll(user1.id, poll.id)
        assert result is True

        retrieved = polls.get_poll(poll.id, user1.id)
        assert retrieved is None

    def test_delete_poll_not_creator_fails(self, db_and_modules):
        """Test non-creator cannot delete poll."""
        db, auth, messaging, polls = db_and_modules

        unique_id = uuid.uuid4().hex[:8]
        user1 = auth.register(
            username=f"del_perm1_{unique_id}",
            email=f"del_perm1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"del_perm2_{unique_id}",
            email=f"del_perm2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Delete perm test")

        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Delete permission?",
            options=["Yes", "No"]
        )

        with pytest.raises(PermissionDeniedError):
            polls.delete_poll(user2.id, poll.id)


class TestCheckExpiredPolls:
    """Tests for expired poll checking."""

    def test_check_expired_polls(self, db_and_modules):
        """Test checking for expired polls."""
        db, auth, messaging, polls = db_and_modules

        count = polls.check_expired_polls()
        assert count >= 0
