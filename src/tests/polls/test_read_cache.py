"""Regression tests for the core poll read cache.

The hydrated poll reads (option hydration, voter stats, per-option vote
aggregation) are memoized behind a bounded short-TTL cache. These tests pin
the contract that makes that safe:

* a warm read is byte-for-byte identical to a cold one but issues far fewer
  hydration queries (query reduction);
* the cache key is partitioned per viewer, so two users never share a body;
* the channel-access gate runs on *every* read before the cache lookup, so a
  denial is never cached and a revoked membership is not served a warm body;
* every mutation invalidates all viewers' cached bodies (counts are visible to
  other users), verified against a real database for vote/change/remove/close/
  delete.

The lightweight unit harness borrows the real mixin methods and only fakes the
database/messaging edges; the authz gate, key builder, cache and invalidation
path are all the production implementations.
"""

from __future__ import annotations

import uuid

import pytest

from src.core.polls.exceptions import PollNotFoundError
from src.core.polls.manager.base import PollManagerBase
from src.core.polls.manager.lifecycle import LifecycleMixin
from src.core.polls.manager.reading import ReadingMixin
from src.core.polls.manager.voting import VotingMixin
from src.core.polls.models import PollResultsVisibility
from src.core.polls.read_cache import PollReadCache

pytestmark = pytest.mark.unit

POLL = 7000
MSG = 7001
USER_A = 42
USER_B = 99
OUTSIDER = 7


class _CountingDB:
    """Minimal DB stand-in that records queries and serves per-viewer rows."""

    def __init__(self):
        self.queries: list[str] = []
        self.poll_row = {
            "id": POLL,
            "message_id": MSG,
            "question": "Which?",
            "question_encrypted": None,
            "created_by": 7,
            "created_at": 100,
            "ends_at": None,
            "ended_at": None,
            "allow_multiple_choice": False,
            "results_visibility": PollResultsVisibility.ALWAYS.value,
        }
        self.option_rows = [
            {
                "id": 10,
                "poll_id": POLL,
                "text": "a",
                "text_encrypted": None,
                "position": 0,
            },
            {
                "id": 11,
                "poll_id": POLL,
                "text": "b",
                "text_encrypted": None,
                "position": 1,
            },
        ]
        self.voter_stats = {
            USER_A: {"count": 2, "mine": 1},
            USER_B: {"count": 2, "mine": 0},
            OUTSIDER: {"count": 2, "mine": 0},
        }
        self.vote_rows = {
            USER_A: [
                {"option_id": 10, "count": 2, "mine": 1},
                {"option_id": 11, "count": 1, "mine": 0},
            ],
            USER_B: [
                {"option_id": 10, "count": 2, "mine": 0},
                {"option_id": 11, "count": 1, "mine": 0},
            ],
        }

    def fetch_one(self, sql, params=()):
        self.queries.append(sql)
        if "SELECT message_id FROM poll_polls" in sql:
            return {"message_id": MSG}
        if "FROM poll_polls" in sql:
            return dict(self.poll_row)
        if "FROM poll_voters" in sql:
            uid = params[0] if params else None
            return dict(self.voter_stats.get(uid, {"count": 0, "mine": 0}))
        raise AssertionError(f"unexpected fetch_one: {sql}")

    def fetch_all(self, sql, params=()):
        self.queries.append(sql)
        if "FROM poll_options" in sql:
            return [dict(row) for row in self.option_rows]
        if "FROM poll_votes" in sql:
            uid = params[0] if params else None
            return [dict(row) for row in self.vote_rows.get(uid, [])]
        raise AssertionError(f"unexpected fetch_all: {sql}")

    def execute(self, sql, params=(), auto_commit=True):
        self.queries.append(sql)
        return None


class _Manager:
    """Mixin composition with a real cache and fake messaging edges."""

    can_access_poll = ReadingMixin.can_access_poll
    get_poll = ReadingMixin.get_poll
    _row_to_poll = ReadingMixin._row_to_poll
    _voter_stats = ReadingMixin._voter_stats
    _has_voted = ReadingMixin._has_voted
    _voter_count = ReadingMixin._voter_count
    _can_view_results = ReadingMixin._can_view_results
    get_results = VotingMixin.get_results
    _end_poll = LifecycleMixin._end_poll
    _poll_read_key = PollManagerBase._poll_read_key
    _invalidate_poll_read_cache = PollManagerBase._invalidate_poll_read_cache
    _encrypt_polls = False

    def __init__(self, db, allowed=(USER_A, USER_B)):
        self._db = db
        self._poll_read_cache = PollReadCache()
        self.allowed = set(allowed)
        self.gate_calls: list[tuple[int, int]] = []

    def _require_messaging(self):
        return self

    def get_message(self, user_id, message_id):
        self.gate_calls.append((user_id, message_id))
        return {"id": message_id} if user_id in self.allowed else None

    def _get_timestamp(self):
        return 1000


def _count(sqls: list[str], needle: str) -> int:
    return sum(needle in sql for sql in sqls)


def test_warm_detail_read_is_identical_and_reduces_queries():
    db = _CountingDB()
    manager = _Manager(db)

    cold = manager.get_poll(POLL, USER_A)
    cold_queries = list(db.queries)
    db.queries.clear()
    warm = manager.get_poll(POLL, USER_A)
    warm_queries = list(db.queries)

    assert cold == warm
    assert cold is warm  # the hydrated object was served from cache
    # Cold: poll row + option hydration + voter aggregation.
    assert len(cold_queries) == 3
    # Warm: only the poll row (used by the live authz gate), no hydration.
    assert len(warm_queries) == 1
    assert _count(cold_queries, "poll_options") == 1
    assert _count(warm_queries, "poll_options") == 0
    assert _count(warm_queries, "poll_voters") == 0
    # The gate ran on BOTH requests, ahead of the cache lookup.
    assert manager.gate_calls == [(USER_A, MSG), (USER_A, MSG)]
    assert manager._poll_read_cache.stats()["hits"] == 1


def test_warm_results_read_is_identical_and_reduces_queries():
    db = _CountingDB()
    manager = _Manager(db)

    cold = manager.get_results(POLL, USER_A)
    cold_queries = list(db.queries)
    db.queries.clear()
    warm = manager.get_results(POLL, USER_A)
    warm_queries = list(db.queries)

    assert cold == warm
    # Cold: message-id gate + poll row + options + voter stats + vote tally.
    assert len(cold_queries) == 5
    # Warm: just the projected message-id gate; no hydration/aggregation.
    assert len(warm_queries) == 1
    assert _count(warm_queries, "poll_votes") == 0
    assert _count(warm_queries, "poll_options") == 0
    # Gate ran for can_access_poll + get_poll on cold, then again on warm.
    assert len(manager.gate_calls) == 3


def test_per_viewer_keys_do_not_leak():
    db = _CountingDB()
    manager = _Manager(db)

    alice = manager.get_results(POLL, USER_A)
    bob = manager.get_results(POLL, USER_B)
    # Repeat reads now hit each viewer's own entry.
    alice_again = manager.get_results(POLL, USER_A)
    bob_again = manager.get_results(POLL, USER_B)

    assert alice.user_voted is True
    assert bob.user_voted is False
    assert alice is alice_again
    assert bob is bob_again
    assert alice is not bob
    # Two viewers in both namespaces (detail + results).
    assert manager._poll_read_cache.stats()["size"] == 4


def test_denials_are_never_cached_or_served_from_cache():
    db = _CountingDB()
    manager = _Manager(db, allowed=())

    assert manager.get_poll(POLL, OUTSIDER) is None
    with pytest.raises(PollNotFoundError):
        manager.get_results(POLL, OUTSIDER)
    assert len(manager._poll_read_cache) == 0  # nothing cached on denial

    # Access granted: the first successful read must hydrate (was never warm).
    manager.allowed.add(OUTSIDER)
    db.queries.clear()
    poll = manager.get_poll(POLL, OUTSIDER)
    assert poll is not None
    assert _count(db.queries, "poll_options") == 1

    # Access revoked while a warm body exists: the gate still wins.
    manager.allowed.discard(OUTSIDER)
    assert manager.get_poll(POLL, OUTSIDER) is None
    with pytest.raises(PollNotFoundError):
        manager.get_results(POLL, OUTSIDER)


def test_end_poll_mutation_invalidates_every_viewer():
    db = _CountingDB()
    manager = _Manager(db)

    manager.get_poll(POLL, USER_A)
    manager.get_results(POLL, USER_A)
    manager.get_poll(POLL, USER_B)
    manager.get_results(POLL, USER_B)
    assert manager._poll_read_cache.stats()["size"] == 4

    manager._end_poll(POLL)  # real mutation path (also used by close/expiry)
    assert len(manager._poll_read_cache) == 0

    db.queries.clear()
    manager.get_results(POLL, USER_A)
    assert _count(db.queries, "poll_votes") == 1  # recomputed from the DB


@pytest.mark.integration
def test_every_mutation_invalidates_warm_reads_real_db(
    poll_manager, auth_manager, messaging_manager
):
    """Vote/change/remove/close/delete all bust warm bodies for all viewers."""
    tag = uuid.uuid4().hex[:8]
    creator = auth_manager.register(
        username=f"cache_creator_{tag}",
        email=f"cache_creator_{tag}@example.com",
        password="TestPass123!",
    )
    viewer = auth_manager.register(
        username=f"cache_viewer_{tag}",
        email=f"cache_viewer_{tag}@example.com",
        password="TestPass123!",
    )
    dm = messaging_manager.create_dm(creator.id, viewer.id)
    msg = messaging_manager.send_message(creator.id, dm.id, "cache test")
    poll = poll_manager.create_poll(
        user_id=creator.id,
        message_id=msg.id,
        question="Cache test?",
        options=["A", "B"],
    )
    cache = poll_manager._poll_read_cache
    cache.clear()

    opt_a, opt_b = poll.options[0].id, poll.options[1].id

    # Warm the creator's view (creator has not voted).
    assert poll_manager.get_results(poll.id, creator.id).total_votes == 0

    # vote -> creator's stale entry must be gone.
    poll_manager.vote(viewer.id, poll.id, [opt_a])
    assert poll_manager.get_results(poll.id, creator.id).total_votes == 1

    # change_vote -> per-option tallies must refresh for the other viewer.
    poll_manager.get_results(poll.id, creator.id)
    poll_manager.change_vote(viewer.id, poll.id, [opt_b])
    tallies = {
        o.id: o.vote_count
        for o in poll_manager.get_results(poll.id, creator.id).options
    }
    assert tallies == {opt_a: 0, opt_b: 1}

    # remove_vote -> headcount must refresh.
    poll_manager.get_results(poll.id, creator.id)
    poll_manager.remove_vote(viewer.id, poll.id)
    assert poll_manager.get_results(poll.id, creator.id).total_votes == 0

    # close_poll -> is_ended must refresh after the cache was warm.
    poll_manager.get_poll(poll.id, creator.id)
    poll_manager.close_poll(creator.id, poll.id)
    assert poll_manager.get_poll(poll.id, creator.id).is_ended is True

    # delete_poll -> no warm bodies may survive the delete.
    poll_manager.get_results(poll.id, creator.id)
    poll_manager.delete_poll(creator.id, poll.id)
    assert len(cache) == 0
    with pytest.raises(PollNotFoundError):
        poll_manager.get_results(poll.id, creator.id)
