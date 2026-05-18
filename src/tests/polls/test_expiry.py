"""
Tests for poll expiry and closing.
"""

import pytest
import uuid
from unittest.mock import patch
from src.core.polls import (
    PollEndedError,
    PermissionDeniedError,
)


class TestClosePoll:
    """Tests for closing polls early."""

    def test_close_poll_success(self, auth_manager, messaging_manager, poll_manager):
        """Test closing a poll successfully."""
        from src.utils import encryption

        unique_id = uuid.uuid4().hex[:8]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"close_user1_{unique_id}",
                email=f"close_user1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"close_user2_{unique_id}",
                email=f"close_user2_{unique_id}@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Close test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Close early?",
            options=["Yes", "No"],
            duration_hours=24,
        )

        closed = poll_manager.close_poll(user1.id, poll.id)

        assert closed.is_ended is True
        assert closed.ended_at is not None

    def test_close_poll_not_creator_fails(
        self, auth_manager, messaging_manager, poll_manager
    ):
        """Test non-creator cannot close poll."""
        from src.utils import encryption

        unique_id = uuid.uuid4().hex[:8]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"perm_user1_{unique_id}",
                email=f"perm_user1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"perm_user2_{unique_id}",
                email=f"perm_user2_{unique_id}@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Permission test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Permission test?",
            options=["Yes", "No"],
        )

        with pytest.raises(PermissionDeniedError):
            poll_manager.close_poll(user2.id, poll.id)

    def test_close_poll_already_ended_fails(
        self, auth_manager, messaging_manager, poll_manager
    ):
        """Test closing already ended poll fails."""
        from src.utils import encryption

        unique_id = uuid.uuid4().hex[:8]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"ended_user1_{unique_id}",
                email=f"ended_user1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"ended_user2_{unique_id}",
                email=f"ended_user2_{unique_id}@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Already ended test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Already ended?",
            options=["Yes", "No"],
        )

        poll_manager.close_poll(user1.id, poll.id)

        with pytest.raises(PollEndedError):
            poll_manager.close_poll(user1.id, poll.id)

    def test_vote_on_closed_poll_fails(
        self, auth_manager, messaging_manager, poll_manager
    ):
        """Test voting on closed poll fails."""
        from src.utils import encryption

        unique_id = uuid.uuid4().hex[:8]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"vote_closed1_{unique_id}",
                email=f"vote_closed1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"vote_closed2_{unique_id}",
                email=f"vote_closed2_{unique_id}@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Vote closed test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Vote after close?",
            options=["Yes", "No"],
        )

        poll_manager.close_poll(user1.id, poll.id)

        with pytest.raises(PollEndedError):
            poll_manager.vote(user2.id, poll.id, [poll.options[0].id])


class TestDeletePoll:
    """Tests for deleting polls."""

    def test_delete_poll_success(self, auth_manager, messaging_manager, poll_manager):
        """Test deleting a poll successfully."""
        from src.utils import encryption

        unique_id = uuid.uuid4().hex[:8]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"del_user1_{unique_id}",
                email=f"del_user1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"del_user2_{unique_id}",
                email=f"del_user2_{unique_id}@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Delete test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Delete me?",
            options=["Yes", "No"],
        )

        result = poll_manager.delete_poll(user1.id, poll.id)
        assert result is True

        retrieved = poll_manager.get_poll(poll.id, user1.id)
        assert retrieved is None

    def test_delete_poll_not_creator_fails(
        self, auth_manager, messaging_manager, poll_manager
    ):
        """Test non-creator cannot delete poll."""
        from src.utils import encryption

        unique_id = uuid.uuid4().hex[:8]
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user1 = auth_manager.register(
                username=f"del_perm1_{unique_id}",
                email=f"del_perm1_{unique_id}@example.com",
                password="TestPass123!",
            )
            user2 = auth_manager.register(
                username=f"del_perm2_{unique_id}",
                email=f"del_perm2_{unique_id}@example.com",
                password="TestPass123!",
            )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Delete perm test")

        poll = poll_manager.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Delete permission?",
            options=["Yes", "No"],
        )

        with pytest.raises(PermissionDeniedError):
            poll_manager.delete_poll(user2.id, poll.id)


class TestCheckExpiredPolls:
    """Tests for expired poll checking."""

    def test_check_expired_polls(self, poll_manager):
        """Test checking for expired polls."""
        count = poll_manager.check_expired_polls()
        assert count >= 0
