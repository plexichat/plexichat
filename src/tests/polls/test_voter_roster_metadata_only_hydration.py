"""Voter roster reads must not hydrate poll data they immediately discard.

``get_voters`` used to call ``get_poll``, which fetches the whole option list
and runs the voter-headcount conditional aggregate on every call -- even though
the roster decision only reads ``created_by``, ``results_visibility`` and the
poll's end-time.  Both of those queries were pure overhead on the roster path
(and both hit the poll's tables for the same poll).  The regression pins a
metadata-only read: one poll-row fetch plus the roster query, no options and no
headcount, while keeping the creator gate, the visibility rules and the live
channel-access gate intact.
"""

import pytest

pytestmark = [pytest.mark.polls, pytest.mark.unit]


class _QuerySpy:
    """Delegate to the real DB while recording every read statement."""

    def __init__(self, real):
        self._real = real
        self.reads = []

    def __getattr__(self, name):
        return getattr(self._real, name)

    def fetch_one(self, query, params=None):
        self.reads.append(" ".join(query.split()))
        return self._real.fetch_one(query, params)

    def fetch_all(self, query, params=None):
        self.reads.append(" ".join(query.split()))
        return self._real.fetch_all(query, params)


def _spy_reads(poll_manager):
    real = poll_manager._db
    spy = _QuerySpy(real)
    poll_manager._db = spy
    return real, spy


def test_roster_uses_metadata_only_reads(poll_manager, user_with_dm):
    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(user.id, msg.id, "Question?", ["A", "B"])
    poll_manager.vote(user.id, poll.id, [poll.options[0].id])
    poll_manager.vote(friend.id, poll.id, [poll.options[1].id])
    poll_manager._poll_read_cache.clear()

    real, spy = _spy_reads(poll_manager)
    try:
        voters = poll_manager.get_voters(poll.id, int(user.id))
    finally:
        poll_manager._db = real

    assert voters == sorted([int(user.id), int(friend.id)])
    # Exactly two reads: the poll row and the roster.  No option fetch and no
    # voter-count aggregate (both were fetched and discarded before the fix).
    assert len(spy.reads) == 2, spy.reads
    assert spy.reads[0].startswith("SELECT * FROM poll_polls")
    assert spy.reads[1].startswith("SELECT user_id FROM poll_voters")
    assert not any("poll_options" in q for q in spy.reads)
    assert not any("COUNT(" in q for q in spy.reads)


def test_hidden_roster_still_hidden_after_vote(poll_manager, user_with_dm):
    from src.core.polls.exceptions import PermissionDeniedError
    from src.core.polls.models import PollResultsVisibility

    user, friend, _dm, msg = user_with_dm
    poll = poll_manager.create_poll(
        user.id,
        msg.id,
        "Question?",
        ["A", "B"],
        results_visibility=PollResultsVisibility.AFTER_VOTE,
    )
    poll_manager.vote(user.id, poll.id, [poll.options[0].id])

    # Creator is entitled to the roster gate but AFTER_VOTE hides the roster.
    assert poll_manager.get_voters(poll.id, int(user.id)) == []
    # A non-creator without the moderator permission is still denied.
    with pytest.raises(PermissionDeniedError):
        poll_manager.get_voters(poll.id, int(friend.id))
