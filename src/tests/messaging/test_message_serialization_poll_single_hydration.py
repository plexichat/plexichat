"""A poll-backed message must hydrate its poll only once during serialization.

``_message_to_response`` used to call ``polls.get_poll()`` to build the
``poll`` field and then ``polls.get_results()`` for the ``poll_results``
field. ``get_results`` already hydrates the very same poll (it calls
``get_poll`` internally), so every poll-bearing message on a list/search/
pins page paid for two full poll-row + option + voter-stat hydration passes
and two authorization gates. This is the N+1 hot path: a page of 100 poll
messages doubled that work.

The fix derives the ``poll`` field from ``results.poll`` that
``get_results`` already returned, eliminating the redundant standalone
``get_poll`` call while keeping both response fields byte-for-byte identical.
"""

from __future__ import annotations

import pytest

import src.api as api
from src.api.routes.messages.helpers import _message_to_response
from src.core.polls.models import Poll, PollOption, PollResults, PollResultsVisibility

pytestmark = [pytest.mark.messaging, pytest.mark.unit]

POLL_ID = 900
MSG_ID = 1000
VIEWER = 42


def _poll(user_id: int) -> Poll:
    return Poll(
        id=POLL_ID,
        message_id=MSG_ID,
        question="Q?",
        created_by=7,
        created_at=100,
        ends_at=None,
        ended_at=None,
        allow_multiple_choice=False,
        results_visibility=PollResultsVisibility.ALWAYS,
        options=[
            PollOption(id=10, poll_id=POLL_ID, text="a", position=0),
            PollOption(id=11, poll_id=POLL_ID, text="b", position=1),
        ],
        total_votes=2,
        is_ended=False,
    )


class _CountingPolls:
    """Polls-module stand-in that models the real get_results -> get_poll graph.

    Each ``get_poll`` is a full hydration (poll row + options + voter stats),
    so the number of ``get_poll`` invocations is the number of expensive
    hydration passes a serialized message triggers.
    """

    def __init__(self):
        self.get_poll_calls = 0
        self.get_results_calls = 0

    def get_poll(self, poll_id, user_id):
        self.get_poll_calls += 1
        return _poll(user_id)

    def get_results(self, poll_id, user_id):
        self.get_results_calls += 1
        return PollResults(
            poll=self.get_poll(poll_id, user_id),  # models real get_results
            options=[
                PollOption(id=10, poll_id=POLL_ID, text="a", position=0, vote_count=2),
                PollOption(id=11, poll_id=POLL_ID, text="b", position=1, vote_count=0),
            ],
            total_votes=2,
            user_voted=True,
            user_votes=[10],
        )


def _message():
    return {
        "id": MSG_ID,
        "author_id": 7,
        "content": "vote!",
        "created_at": 1_700_000_000_000,
        "channel_id": 55,
        "metadata": {"poll_id": POLL_ID},
        "embeds": [],
    }


def test_poll_hydrated_once_per_message(monkeypatch):
    fake = _CountingPolls()
    monkeypatch.setattr(api, "get_polls", lambda: fake)

    resp = _message_to_response(_message(), viewer_user_id=VIEWER, suppress_embeds=True)

    # get_results hydrates the poll internally; the serializer must not do it
    # a second time just to populate the ``poll`` field.
    assert fake.get_results_calls == 1
    assert fake.get_poll_calls == 1  # ONLY the one inside get_results

    # Both response fields are still fully populated and consistent.
    assert resp.poll is not None
    assert resp.poll_results is not None
    assert resp.poll.id == str(POLL_ID)
    assert [o.id for o in resp.poll.options] == ["10", "11"]
    assert [o.vote_count for o in resp.poll.options] == [None, None]
    assert [o.vote_count for o in resp.poll_results.options] == [2, 0]
    assert resp.poll_results.total_votes == 2
    assert resp.poll_results.user_voted is True


def test_no_poll_message_never_touches_polls_module(monkeypatch):
    fake = _CountingPolls()
    monkeypatch.setattr(api, "get_polls", lambda: fake)

    msg = _message()
    msg["metadata"] = {"k": "v"}
    resp = _message_to_response(msg, viewer_user_id=VIEWER, suppress_embeds=True)

    assert fake.get_poll_calls == 0
    assert fake.get_results_calls == 0
    assert resp.poll is None
    assert resp.poll_results is None
