"""Regression tests for poll cluster fixes.

Covers:
- close_poll / delete_poll creator checks normalize int/str (F10), so the
  postgres driver's string user IDs cannot deny legitimate creators.
- create_poll raises PollAlreadyExistsError (a 409-class domain error) when
  the message already carries a poll, instead of leaking a raw driver
  IntegrityError out of the module.
"""

import pytest

from src.core.polls.exceptions import PermissionDeniedError, PollAlreadyExistsError


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


@pytest.mark.polls
class TestOnePollPerMessage:
    """A message may carry at most one poll (``poll_polls.message_id UNIQUE``).

    The second ``create_poll`` on the same message must surface as
    ``PollAlreadyExistsError``.  Before the fix it escaped as the raw
    driver ``IntegrityError``, which the route layer mapped to a 500 and
    callers could not distinguish from a genuine server fault.
    """

    def test_duplicate_poll_on_same_message_raises_domain_error(self, dm_with_message):
        """Creating a second poll on a message that already has one."""
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="First poll?",
            options=["Yes", "No"],
        )
        with pytest.raises(PollAlreadyExistsError):
            polls.create_poll(
                user_id=user1.id,
                message_id=msg.id,
                question="Second poll?",
                options=["Yes", "No"],
            )

    def test_duplicate_poll_leaves_the_original_intact(self, dm_with_message):
        """The rejected second poll must not disturb the first one.

        The insert of the duplicate happens inside a transaction that is
        rolled back, so the original poll's question/options and the
        absence of stray option rows are both verified here.
        """
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message
        first = polls.create_poll(
            user_id=user1.id,
            message_id=msg.id,
            question="Keep me?",
            options=["Red", "Blue"],
        )

        with pytest.raises(PollAlreadyExistsError):
            polls.create_poll(
                user_id=user1.id,
                message_id=msg.id,
                question="Clobber me?",
                options=["Green", "Yellow", "Purple"],
            )

        still_there = polls.get_poll(first.id, user1.id)
        assert still_there is not None
        assert still_there.question == "Keep me?"
        assert [opt.text for opt in still_there.options] == ["Red", "Blue"]

    def test_duplicate_detection_survives_a_racing_insert(self, dm_with_message):
        """A poll that lands after the pre-check still gets a 409, not a 500.

        The SELECT pre-check alone cannot close the concurrent-INSERT
        window, so the UNIQUE violation must be translated into the same
        domain error.  This test hides the winner from the pre-check
        (forcing the INSERT to be the guard that fires) and asserts the
        caller still sees ``PollAlreadyExistsError`` rather than a raw
        driver ``IntegrityError``.
        """
        user1, _user2, _dm, msg, polls, _messaging = dm_with_message

        real_fetch_one = polls._db.fetch_one

        def hiding_fetch_one(query, params=None, *args, **kwargs):
            # Skip only the manager's existence pre-check; everything else
            # (the racing INSERT's own reads, the get_poll re-fetch)
            # goes through untouched.
            if "FROM poll_polls WHERE message_id" in query:
                real_fetch_one(
                    "INSERT INTO poll_polls (id, message_id, question, "
                    "created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                    (winner_id, msg.id, "Winner", user1.id, 1),
                )
                return None
            return real_fetch_one(query, params, *args, **kwargs)

        winner_id = polls._generate_id()
        polls._db.fetch_one = hiding_fetch_one
        try:
            with pytest.raises(PollAlreadyExistsError):
                polls.create_poll(
                    user_id=user1.id,
                    message_id=msg.id,
                    question="Loser?",
                    options=["Yes", "No"],
                )
        finally:
            polls._db.fetch_one = real_fetch_one
