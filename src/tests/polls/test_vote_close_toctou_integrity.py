"""Voting writes must not land after the poll closes (check-then-write race).

The ended/deadline check in ``vote`` / ``change_vote`` / ``remove_vote`` runs
on a poll snapshot hydrated *before* the write transaction.  A creator close
(or the periodic expiry sweep) that lands between that check and the INSERT
used to be invisible to the write, so a vote could be recorded on an already
closed poll -- altering the recorded outcome of a closed poll and inflating
its authoritative counts.

These tests close the poll exactly at the moment the vote transaction begins
(i.e. after every pre-write check) and assert the write is rejected and no
vote/voter rows are left behind.
"""

import pytest

from src.core.polls import PollEndedError


def _close_on_transaction_begin(manager, poll_id):
    """Return a begin_transaction wrapper that closes ``poll_id`` first.

    This deterministically reproduces a concurrent close/expiry landing after
    the manager's pre-write ``is_ended`` check but before its writes commit.
    """
    real_begin = manager._db.begin_transaction

    def wrapped():
        manager._db.execute(
            "UPDATE poll_polls SET ended_at = ? WHERE id = ?",
            (manager._get_timestamp(), poll_id),
        )
        return real_begin()

    return wrapped, real_begin


@pytest.mark.polls
def test_vote_cannot_land_after_poll_closed_mid_request(poll_manager, user_with_dm):
    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(user.id, msg.id, "Race?", ["A", "B"])
    option_id = poll.options[0].id
    assert poll.ends_at is None  # no deadline: only an explicit close can end it

    wrapped, real_begin = _close_on_transaction_begin(poll_manager, poll.id)
    poll_manager._db.begin_transaction = wrapped
    try:
        with pytest.raises(PollEndedError):
            poll_manager.vote(friend.id, poll.id, [option_id])
    finally:
        poll_manager._db.begin_transaction = real_begin

    # The closed poll's recorded outcome must be untouched.
    assert (
        poll_manager._db.fetch_one(
            "SELECT 1 FROM poll_votes WHERE poll_id = ? AND user_id = ?",
            (poll.id, friend.id),
        )
        is None
    )
    assert (
        poll_manager._db.fetch_one(
            "SELECT 1 FROM poll_voters WHERE poll_id = ? AND user_id = ?",
            (poll.id, friend.id),
        )
        is None
    )


@pytest.mark.polls
def test_change_vote_cannot_land_after_poll_closed_mid_request(
    poll_manager, user_with_dm
):
    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(user.id, msg.id, "Race2?", ["A", "B"])
    poll_manager.vote(friend.id, poll.id, [poll.options[0].id])
    new_option = poll.options[1].id

    wrapped, real_begin = _close_on_transaction_begin(poll_manager, poll.id)
    poll_manager._db.begin_transaction = wrapped
    try:
        with pytest.raises(PollEndedError):
            poll_manager.change_vote(friend.id, poll.id, [new_option])
    finally:
        poll_manager._db.begin_transaction = real_begin

    # The original selection survives a change that raced with the close.
    rows = poll_manager._db.fetch_all(
        "SELECT option_id FROM poll_votes WHERE poll_id = ? AND user_id = ?",
        (poll.id, friend.id),
    )
    assert [row["option_id"] for row in rows] == [poll.options[0].id]
