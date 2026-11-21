"""Fan-out message broadcasts must not carry per-viewer poll results.

Message-delete and AutoMod tombstone serializers call ``_message_to_response``
with no concrete per-request viewer (``viewer_user_id=None``) but pass the
message author as ``poll_viewer_user_id`` so the poll UI survives the client
merge.  That single serialized body is then broadcast to *every* channel
recipient.  For an AFTER_VOTE / AFTER_END poll on which the author is entitled
to results, ``get_results(author)`` returns per-option vote counts, the voter
headcount and the author's own individual votes.  Embedding those in a fan-out
body hands them to members who are not entitled to see them -- a
results-visibility bypass independent of the channel-view and messages.send
gates.

The fix keeps the non-sensitive poll skeleton (question / options) but drops
the viewer-specific totals for fan-out serializations.
"""

from __future__ import annotations

import pytest

import src.api as api
from src.api.routes.messages.helpers import _message_to_response
from src.core.polls.models import Poll, PollOption, PollResults, PollResultsVisibility

pytestmark = [pytest.mark.messaging, pytest.mark.unit]

POLL_ID = 900
MSG_ID = 1000
AUTHOR = 7
OTHER_VIEWER = 42


def _author_visible_results() -> PollResults:
    """Results as ``get_results()`` returns them for an entitled author."""
    poll = Poll(
        id=POLL_ID,
        message_id=MSG_ID,
        question="Secret poll",
        created_by=AUTHOR,
        created_at=100,
        results_visibility=PollResultsVisibility.AFTER_VOTE,
        options=[
            PollOption(id=10, poll_id=POLL_ID, text="a", position=0),
            PollOption(id=11, poll_id=POLL_ID, text="b", position=1),
        ],
        total_votes=3,  # headcount visible to the author, not to other members
        is_ended=False,
    )
    return PollResults(
        poll=poll,
        options=[
            PollOption(id=10, poll_id=POLL_ID, text="a", position=0, vote_count=2),
            PollOption(id=11, poll_id=POLL_ID, text="b", position=1, vote_count=1),
        ],
        total_votes=3,
        user_voted=True,
        user_votes=[10],  # the author's individual vote
    )


class _RecordingPolls:
    """Polls-module stand-in that records which viewer results were built for."""

    def __init__(self, results: PollResults):
        self.results = results
        self.calls = []

    def get_results(self, poll_id, user_id):
        self.calls.append((poll_id, user_id))
        return self.results


def _message() -> dict:
    return {
        "id": MSG_ID,
        "author_id": AUTHOR,
        "content": "vote!",
        "created_at": 1_700_000_000_000,
        "channel_id": 55,
        "metadata": {"poll_id": POLL_ID},
        "embeds": [],
    }


def test_fanout_broadcast_does_not_leak_poll_results(monkeypatch):
    fake = _RecordingPolls(_author_visible_results())
    monkeypatch.setattr(api, "get_polls", lambda: fake)

    # Both fan-out producers (delete message / AutoMod tombstone) pass the
    # author as poll context viewer and no concrete request viewer.
    resp = _message_to_response(
        _message(), poll_viewer_user_id=AUTHOR, suppress_embeds=True
    )

    assert fake.calls == [(POLL_ID, AUTHOR)]
    # The non-sensitive skeleton survives for UI continuity ...
    assert resp.poll is not None
    assert [o.id for o in resp.poll.options] == ["10", "11"]
    # ... but no per-viewer totals or individual-vote payload reach the
    # broadcast audience.
    assert resp.poll.total_votes is None
    assert all(o.vote_count is None for o in resp.poll.options)
    assert resp.poll_results is None


def test_per_viewer_serialization_still_includes_poll_results(monkeypatch):
    """Guard against a blanket disable: real request paths keep full results."""
    fake = _RecordingPolls(_author_visible_results())
    monkeypatch.setattr(api, "get_polls", lambda: fake)

    resp = _message_to_response(
        _message(), viewer_user_id=OTHER_VIEWER, suppress_embeds=True
    )

    assert fake.calls == [(POLL_ID, OTHER_VIEWER)]
    assert resp.poll_results is not None
    assert resp.poll_results.total_votes == 3
    assert [o.vote_count for o in resp.poll_results.options] == [2, 1]
    assert resp.poll_results.user_votes == ["10"]
