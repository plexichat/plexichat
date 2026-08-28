"""Regression tests for poll cluster fixes.

Covers:
- close_poll / delete_poll creator checks normalize int/str (F10), so the
  postgres driver's string user IDs cannot deny legitimate creators.
"""

import pytest

from src.core.polls.exceptions import PermissionDeniedError


@pytest.mark.polls
class TestCreatorTypeNormalization:
    """F10: creator checks must not compare int vs str directly."""

    def test_close_poll_accepts_string_creator_id(self, dm_with_message):
        """A string creator id (postgres driver shape) must still close the poll."""
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Close me?",
            options=["Yes", "No"],
        )
        closed = polls.close_poll(str(user1.id), poll.id)
        assert closed.is_ended is True

    def test_delete_poll_accepts_string_creator_id(self, dm_with_message):
        """A string creator id must still delete the poll."""
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Delete me?",
            options=["Yes", "No"],
        )
        assert polls.delete_poll(str(user1.id), poll.id) is True
        # get_poll returns None (not raise) for missing polls.
        assert polls.get_poll(poll.id, user1.id) is None

    def test_close_poll_still_denies_non_creator(self, dm_with_message):
        """The normalization must not weaken the permission boundary."""
        user1, user2, _dm, msg, polls, _messaging = dm_with_message
        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Owner only?",
            options=["Yes", "No"],
        )
        with pytest.raises(PermissionDeniedError):
            polls.close_poll(str(user2.id), poll.id)

    def test_close_poll_accepts_string_created_by_row(
        self, dm_with_message, monkeypatch
    ):
        """A created_by stored as TEXT (postgres) must be normalized on read.

        Seeds the row with a string creator id directly so the DB-driven
        drift the finding describes is exercised end-to-end.
        """
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        poll = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Seeded?",
            options=["Yes", "No"],
        )
        polls._db.execute(
            "UPDATE poll_polls SET created_by = ? WHERE id = ?",
            (str(user1.id), poll.id),
        )
        closed = polls.close_poll(user1.id, poll.id)
        assert closed.is_ended is True
