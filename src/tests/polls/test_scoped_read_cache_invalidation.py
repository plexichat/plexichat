"""A single poll mutation must not flush every other poll's warm read bodies.

Before the fix, ``_invalidate_poll_read_cache()`` called ``cache.clear()`` on
every mutation. Because the cache is shared by all polls and keyed per viewer,
one vote on poll X discarded the hydrated detail/results of every other poll,
forcing a full re-hydration storm on the next read of each. The fix scopes
invalidation to the poll that actually changed while keeping the legacy full
flush for the multi-poll expiry sweep.

These tests pin both halves of the contract:

* the mutated poll's entries are dropped (no stale body can be served), and
* every other poll's entries stay warm (no unrelated re-hydration).
"""

from __future__ import annotations

import uuid

import pytest

from src.core.polls.manager.base import PollManagerBase
from src.core.polls.manager.lifecycle import LifecycleMixin
from src.core.polls.manager.reading import ReadingMixin
from src.core.polls.manager.voting import VotingMixin
from src.core.polls.models import PollResultsVisibility
from src.core.polls.read_cache import PollReadCache

pytestmark = [pytest.mark.polls, pytest.mark.unit]

POLL_A = 8100
POLL_B = 8200
VIEWERS = (301, 302, 303)


# --- PollReadCache.invalidate_matching -----------------------------------


def test_invalidate_matching_is_scoped_and_keeps_byte_accounting():
    cache = PollReadCache()
    for pid in (POLL_A, POLL_B):
        for uid in VIEWERS:
            for kind in ("detail", "results"):
                cache.set((kind, pid, uid), f"body-{kind}-{pid}-{uid}")

    assert len(cache) == 12
    total_bytes = cache.stats()["bytes"]
    assert total_bytes > 0

    removed = cache.invalidate_matching(lambda key: key[1] == POLL_A)

    assert removed == 6
    assert len(cache) == 6
    assert all(key[1] == POLL_B for key in cache._data)
    assert cache.stats()["bytes"] < total_bytes
    assert cache.stats()["bytes"] > 0
    assert cache.stats()["scoped_invalidations"] == 6
    # Warm entries for the untouched poll are still served.
    assert (
        cache.get(("results", POLL_B, VIEWERS[0]))
        == f"body-results-{POLL_B}-{VIEWERS[0]}"
    )


def test_invalidate_matching_frees_capacity_without_evicting_siblings():
    """Scoped removal frees both the entry and byte budget exactly."""
    cache = PollReadCache(max_entries=4)
    for pid in (POLL_A, POLL_B):
        cache.set(("results", pid, VIEWERS[0]), "x" * 100)

    assert cache.stats()["bytes"] == 200
    cache.invalidate_matching(lambda key: key[1] == POLL_A)

    assert len(cache) == 1
    assert cache.stats()["bytes"] == 100  # only poll A's bytes were released
    assert cache.stats()["evictions"] == 0  # scoped removal is not an LRU eviction

    # The freed capacity is reusable; sibling entries are not evicted early.
    cache.set(("results", POLL_A, VIEWERS[1]), "y" * 100)
    cache.set(("results", POLL_B, VIEWERS[1]), "y" * 100)
    cache.set(("results", POLL_A, VIEWERS[2]), "y" * 100)
    assert len(cache) == 4
    assert cache.stats()["bytes"] == 400
    assert cache.stats()["evictions"] == 0


# --- manager-level behaviour via the real mixins -------------------------


class _CountingDB:
    """DB stand-in that counts the expensive hydration queries per poll."""

    def __init__(self):
        self.hydration = []
        self.voter_counts = {POLL_A: 0, POLL_B: 0}

    def _row(self, pid):
        return {
            "id": pid,
            "message_id": 10_000 + pid,
            "question": f"Q{pid}?",
            "question_encrypted": None,
            "created_by": 7,
            "created_at": 100,
            "ends_at": None,
            "ended_at": None,
            "allow_multiple_choice": False,
            "results_visibility": PollResultsVisibility.ALWAYS.value,
        }

    def fetch_one(self, sql, params=()):
        if "SELECT message_id FROM poll_polls" in sql:
            return {"message_id": 10_000 + params[0]}
        if "FROM poll_polls" in sql:
            return self._row(params[0])
        if "FROM poll_voters" in sql:
            return {"count": self.voter_counts.get(params[0], 0), "mine": 0}
        return None

    def fetch_all(self, sql, params=()):
        if "FROM poll_options" in sql or "FROM poll_votes" in sql:
            self.hydration.append(
                ("options" if "poll_options" in sql else "votes", params[0])
            )
        if "FROM poll_options" in sql:
            return [
                {
                    "id": params[0] * 10,
                    "poll_id": params[0],
                    "text": "a",
                    "text_encrypted": None,
                    "position": 0,
                }
            ]
        if "FROM poll_votes" in sql:
            return []
        return []


class _Manager:
    """Borrow the real mixin methods; only the DB/messaging edges are faked."""

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

    def __init__(self, db):
        self._db = db
        self._poll_read_cache = PollReadCache()

    def _require_messaging(self):
        return self

    def get_message(self, user_id, message_id):
        return {"id": message_id}

    def _get_timestamp(self):
        return 1000


def _warm(mgr):
    bodies = {}
    for pid in (POLL_A, POLL_B):
        for uid in VIEWERS:
            bodies[(pid, uid, "detail")] = mgr.get_poll(pid, uid)
            bodies[(pid, uid, "results")] = mgr.get_results(pid, uid)
    return bodies


def test_mutation_drops_only_the_affected_polls_entries():
    db = _CountingDB()
    mgr = _Manager(db)
    warm = _warm(mgr)
    assert len(mgr._poll_read_cache) == 12
    db.hydration.clear()

    # Simulate a mutation to poll A only (the mutation methods call this with
    # the poll they changed). Poll B's headcount actually changes in the DB.
    db.voter_counts[POLL_B] = 7
    mgr._invalidate_poll_read_cache(POLL_A)

    assert len(mgr._poll_read_cache) == 6  # only poll A's six entries dropped

    # Poll B stays warm: same object identity, no re-hydration, stale count
    # (the real mutation would have invalidated it if it had changed).
    assert mgr.get_results(POLL_B, VIEWERS[0]) is warm[(POLL_B, VIEWERS[0], "results")]
    assert mgr.get_poll(POLL_B, VIEWERS[0]) is warm[(POLL_B, VIEWERS[0], "detail")]
    assert db.hydration == []

    # Poll A is cold: a fresh object is hydrated from the DB.
    assert (
        mgr.get_results(POLL_A, VIEWERS[0]) is not warm[(POLL_A, VIEWERS[0], "results")]
    )
    assert any(pid == POLL_A for _kind, pid in db.hydration)


def test_no_ids_keeps_the_full_flush_for_multi_poll_sweeps():
    db = _CountingDB()
    mgr = _Manager(db)
    _warm(mgr)
    assert len(mgr._poll_read_cache) == 12

    mgr._invalidate_poll_read_cache()  # expiry-sweep path

    assert len(mgr._poll_read_cache) == 0


def test_poll_id_type_is_normalized():
    """The API passes ids as strings; invalidation must still find the entries."""
    db = _CountingDB()
    mgr = _Manager(db)
    warm = _warm(mgr)

    mgr._invalidate_poll_read_cache(str(POLL_A))

    assert len(mgr._poll_read_cache) == 6
    assert mgr.get_results(POLL_B, VIEWERS[0]) is warm[(POLL_B, VIEWERS[0], "results")]


@pytest.mark.integration
def test_real_vote_only_invalidates_the_voted_poll(
    poll_manager, auth_manager, messaging_manager
):
    """End-to-end guard with the real database and the production mixins."""
    tag = uuid.uuid4().hex[:8]
    creator = auth_manager.register(
        username=f"scope_c_{tag}",
        email=f"scope_c_{tag}@example.com",
        password="TestPass123!",
    )
    viewer = auth_manager.register(
        username=f"scope_v_{tag}",
        email=f"scope_v_{tag}@example.com",
        password="TestPass123!",
    )
    dm = messaging_manager.create_dm(creator.id, viewer.id)

    polls = []
    for index in range(2):
        msg = messaging_manager.send_message(creator.id, dm.id, f"Scope {index}")
        polls.append(
            poll_manager.create_poll(
                user_id=creator.id,
                message_id=msg.id,
                question=f"Scope {index}?",
                options=["A", "B"],
            )
        )
    poll_a, poll_b = polls
    cache = poll_manager._poll_read_cache
    cache.clear()

    warm_b = poll_manager.get_results(poll_b.id, creator.id)
    warm_a = poll_manager.get_results(poll_a.id, creator.id)
    assert cache.stats()["size"] >= 2

    # A real vote on poll A only.
    poll_manager.vote(viewer.id, poll_a.id, [poll_a.options[0].id])

    # Poll B's body survived the vote on A and is still the warm object.
    assert poll_manager.get_results(poll_b.id, creator.id) is warm_b
    # Poll A's body was dropped and recomputed with the new headcount.
    assert poll_manager.get_results(poll_a.id, creator.id) is not warm_a
    assert poll_manager.get_results(poll_a.id, creator.id).total_votes == 1
