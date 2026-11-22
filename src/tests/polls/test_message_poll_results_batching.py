"""Query-count regression for viewer-specific poll hydration in message serialization.

A list/search page serializes up to 100 messages through
``_message_to_response``.  For every poll-bearing message that serializer used
to call ``polls.get_results(poll_id, viewer)`` individually: each call pays a
message/authz row, the poll row, its options, the voter headcount and the vote
aggregation.  A page of N polls therefore issued O(N) near-identical query
batches even though every poll belongs to the same page and the same viewer.

This test pins the fix: the page prefetches all its poll results for the viewer
in ONE bounded batched lookup and the serializer reads that map.  Query counts
must stay flat as the page grows, with byte-identical serialized output and the
same fail-closed viewer scoping as the per-message path.
"""

import pytest

import src.api as api
from src.api.routes.messages.helpers import _message_to_response
from src.core.database.cache.base import _mem_cache, _mem_cache_lock

pytestmark = pytest.mark.polls


class _ExecuteCounter:
    """Count real DB round trips (``fetch_*`` funnel through ``execute``)."""

    def __init__(self, db):
        self.db = db
        self.count = 0
        self._orig = None

    def __enter__(self):
        self._orig = self.db.execute

        def wrapped(*args, **kwargs):
            self.count += 1
            return self._orig(*args, **kwargs)

        self.db.execute = wrapped
        return self

    def __exit__(self, *_exc):
        self.db.execute = self._orig
        return False


def _clear_caches(poll_manager):
    poll_manager._invalidate_poll_read_cache()
    with _mem_cache_lock:
        _mem_cache.clear()


def _make_poll_page(
    poll_manager, messaging_manager, user, dm, n, visibility=None, vote=False
):
    messages = []
    for i in range(n):
        msg = messaging_manager.send_message(user.id, dm.id, f"poll message {i}")
        kwargs = {"results_visibility": visibility} if visibility else {}
        poll = poll_manager.create_poll(
            user.id, msg.id, f"Question {i}?", ["A", "B"], **kwargs
        )
        if vote:
            poll_manager.vote(user.id, poll.id, [poll.options[0].id])
        # This is how the send path marks a poll-bearing message for the
        # serializer (helpers._message_to_response reads metadata["poll_id"]).
        msg.metadata = {"poll_id": poll.id}
        messages.append(msg)
    return messages


def _serialize_page(db, poll_manager, messages, viewer, use_batch):
    _clear_caches(poll_manager)
    with _ExecuteCounter(db) as counter:
        if use_batch:
            # Imported lazily so the pre-fix tree collects and fails at the
            # assertion (proving the hotspot) instead of at import time.
            from src.api.routes.messages.helpers import batch_poll_results

            poll_map = batch_poll_results(messages, viewer.id, polls_mod=poll_manager)
            bodies = [
                _message_to_response(
                    msg,
                    viewer_user_id=viewer.id,
                    poll_results_data=poll_map,
                    suppress_embeds=True,
                )
                for msg in messages
            ]
        else:
            bodies = [
                _message_to_response(
                    msg, viewer_user_id=viewer.id, suppress_embeds=True
                )
                for msg in messages
            ]
    return counter.count, bodies


@pytest.fixture
def wired_polls(monkeypatch, poll_manager):
    monkeypatch.setattr(api, "get_polls", lambda: poll_manager)
    yield poll_manager
    _clear_caches(poll_manager)


def test_poll_hydration_query_count_is_flat_with_identical_output(
    db, messaging_manager, wired_polls, dm_with_message
):
    user, _friend, dm, _msg, _polls, _messaging = dm_with_message

    small = _make_poll_page(wired_polls, messaging_manager, user, dm, 4, vote=True)
    large = _make_poll_page(wired_polls, messaging_manager, user, dm, 20, vote=True)

    legacy_small, _ = _serialize_page(db, wired_polls, small, user, use_batch=False)
    legacy_large, _ = _serialize_page(db, wired_polls, large, user, use_batch=False)
    batched_small, _ = _serialize_page(db, wired_polls, small, user, use_batch=True)
    batched_large, batched_bodies = _serialize_page(
        db, wired_polls, large, user, use_batch=True
    )
    legacy_large, legacy_bodies = _serialize_page(
        db, wired_polls, large, user, use_batch=False
    )

    # The old path really was an N+1: tripling the poll-bearing messages
    # tripled the query count.
    assert legacy_large > legacy_small, (
        f"no N+1 in the legacy path ({legacy_small} -> {legacy_large}); "
        "the test is not exercising the hotspot"
    )
    # The batched path stays flat as the page grows (one chunk for <=500 ids).
    assert batched_large - batched_small <= 2, (
        f"query count grew with page size: {batched_small} -> {batched_large}"
    )
    assert batched_large < legacy_large, (batched_large, legacy_large)
    # Identical output, including per-option counts and the viewer's own votes.
    assert batched_bodies == legacy_bodies
    assert all(body.poll_results is not None for body in batched_bodies)


def test_bulk_poll_results_fail_closed_and_coerce_ids(
    db, messaging_manager, wired_polls, dm_with_message
):
    """The batched fetch keeps viewer scoping, fail-closed authz and strict ids."""
    user, friend, dm, _msg, _polls, _messaging = dm_with_message
    messages = _make_poll_page(wired_polls, messaging_manager, user, dm, 2)
    poll_ids = [int(m.metadata["poll_id"]) for m in messages]

    # The viewer who owns the DM sees results; an unrelated user does not.
    visible = wired_polls.get_results_bulk(poll_ids, user.id)
    assert set(visible) == set(poll_ids)
    assert all(r is not None for r in visible.values())

    outsider_id = max(poll_ids) + 100_000
    denied = wired_polls.get_results_bulk(poll_ids, outsider_id)
    assert all(r is None for r in denied.values())

    # Strict coercion: bool / fractional float / malformed strings are refused
    # before they can reach SQL, exactly like the reaction bulk-id validator.
    for bad in ([True], [1.5], ["not-an-id"], [-1]):
        with pytest.raises(ValueError):
            wired_polls.get_results_bulk(bad, user.id)


def test_hidden_results_and_own_votes_match_per_message_path(
    db, messaging_manager, wired_polls, dm_with_message
):
    """AFTER_VOTE polls stay hidden for a non-voter, exposed for the voter.

    The batch must not turn a viewer-unentitled body into one that leaks counts,
    nor lose the voter's own selections -- both are compared against the
    original per-message ``get_results`` serialization.
    """
    from src.core.polls.models import PollResultsVisibility

    user, friend, dm, _msg, _polls, _messaging = dm_with_message
    hidden = _make_poll_page(
        wired_polls,
        messaging_manager,
        user,
        dm,
        3,
        visibility=PollResultsVisibility.AFTER_VOTE,
        vote=True,
    )

    for viewer in (user, friend):
        _, legacy_bodies = _serialize_page(
            db, wired_polls, hidden, viewer, use_batch=False
        )
        _, batched_bodies = _serialize_page(
            db, wired_polls, hidden, viewer, use_batch=True
        )
        assert batched_bodies == legacy_bodies
        if viewer.id == user.id:
            assert all(body.poll_results.user_votes for body in batched_bodies)
            assert all(body.poll_results.total_votes == 1 for body in batched_bodies)
        else:
            # Non-voter on an AFTER_VOTE poll: the skeleton is present but
            # every viewer-specific count stays hidden.
            assert all(body.poll_results.user_votes == [] for body in batched_bodies)
            assert all(body.poll_results.total_votes is None for body in batched_bodies)
            assert all(
                opt.vote_count is None
                for body in batched_bodies
                for opt in body.poll_results.options
            )
